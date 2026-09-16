# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER SOL TELEMETRY & OBSERVABILITY ENGINE // UNIT TEST SUITE
Tests for:
1. Safe DOM Telemetry Extraction & Snapshot Parsing
2. Thinking -> Coding Edge-Triggered Transition Detection & Metrics
3. Context Saturation Sentinel & Warning Thresholds
4. S_t = (G, P, T, M, A, X, C) Checkpoint Generation & SHA-256 Chaining
5. SPECTER-DSL Semantic Prompt Templates & Parameter Validation
================================================================================
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from pathlib import Path

# Add current directory to path
CORE_BRIDGE_DIR = Path(__file__).resolve().parent
if str(CORE_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_BRIDGE_DIR))

from sol_telemetry_engine import (
    canonical_json,
    sha256_digest,
    compute_file_sha256,
    SolState,
    DOMTelemetrySnapshot,
    TransitionRecord,
    SolStateTransitionDetector,
    SaturationThresholds,
    PassedTestEvidence,
    TransitionCheckpoint,
    ContextSaturationSentinel,
    SolPromptType,
    SolPromptEngine,
    SolTelemetryEngine
)


class SolTelemetryEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="specter_test_telemetry_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Cryptographic & Canonical Primitives
    # -------------------------------------------------------------------------
    def test_canonical_json_and_sha256(self):
        obj_a = {"b": 2, "a": 1, "nested": {"z": 10, "y": 20}}
        obj_b = {"a": 1, "nested": {"y": 20, "z": 10}, "b": 2}
        
        canon_a = canonical_json(obj_a)
        canon_b = canonical_json(obj_b)
        self.assertEqual(canon_a, canon_b)
        self.assertEqual(sha256_digest(canon_a), sha256_digest(canon_b))

        # Test file SHA-256
        test_file = self.tmp_dir / "test_artifact.py"
        test_file.write_bytes(b"print('specter sovereign')\n")
        file_hash = compute_file_sha256(test_file)
        expected_hash = sha256_digest(b"print('specter sovereign')\n")
        self.assertEqual(file_hash, expected_hash)

    # -------------------------------------------------------------------------
    # 2. DOM Snapshot Parsing & State Machine Transitions
    # -------------------------------------------------------------------------
    def test_dom_snapshot_parsing(self):
        raw_payload = {
            "success": True,
            "timestamp_ms": 1788743822000,
            "is_streaming": True,
            "has_turn": True,
            "turn_chars": 1500,
            "thought": {
                "present": True,
                "active": True,
                "collapsed": False,
                "text_length": 350,
                "duration_text": "Thinking...",
                "duration_seconds": 0.0
            },
            "code": {
                "present": False,
                "blocks_count": 0,
                "languages": [],
                "total_code_chars": 0,
                "first_block_preview": ""
            },
            "saturation": {"detected": False, "reasons": [], "input_disabled": False},
            "error": {"detected": False, "text": ""},
            "model_slug": "gpt-5-6-thinking"
        }
        snap = DOMTelemetrySnapshot.from_dict(raw_payload)
        self.assertTrue(snap.is_streaming)
        self.assertTrue(snap.thought_active)
        self.assertFalse(snap.code_present)
        self.assertEqual(snap.model_slug, "gpt-5-6-thinking")

    def test_thinking_to_coding_transition_lifecycle(self):
        transitions_observed: list[TransitionRecord] = []

        def callback(rec: TransitionRecord):
            transitions_observed.append(rec)

        detector = SolStateTransitionDetector(on_transition=callback)
        self.assertEqual(detector.current_state, SolState.IDLE)

        # Step 1: Prompt submitted (streaming starts, no assistant turn yet)
        snap_sub = DOMTelemetrySnapshot.from_dict({
            "is_streaming": True,
            "has_turn": False,
            "turn_chars": 0
        })
        detector.ingest_snapshot(snap_sub)
        self.assertEqual(detector.current_state, SolState.SUBMITTED)

        # Step 2: Model starts Extended Thinking
        snap_thinking = DOMTelemetrySnapshot.from_dict({
            "is_streaming": True,
            "has_turn": True,
            "turn_chars": 120,
            "thought": {
                "present": True,
                "active": True,
                "collapsed": False,
                "duration_seconds": 0.0
            },
            "code": {"present": False, "blocks_count": 0}
        })
        detector.ingest_snapshot(snap_thinking)
        self.assertEqual(detector.current_state, SolState.THINKING)

        # Step 3: Critical Edge - Thought finishes and first code block streams!
        snap_coding = DOMTelemetrySnapshot.from_dict({
            "is_streaming": True,
            "has_turn": True,
            "turn_chars": 850,
            "thought": {
                "present": True,
                "active": False,
                "collapsed": True,
                "duration_seconds": 8.4,
                "duration_text": "Pensou por 8.4 segundos"
            },
            "code": {
                "present": True,
                "blocks_count": 1,
                "languages": ["python"],
                "total_code_chars": 600,
                "first_block_preview": "def solve(): return True"
            }
        })
        rec_coding = detector.ingest_snapshot(snap_coding)
        self.assertIsNotNone(rec_coding)
        self.assertEqual(detector.current_state, SolState.CODING)
        self.assertEqual(rec_coding.from_state, SolState.THINKING)
        self.assertEqual(rec_coding.to_state, SolState.CODING)
        self.assertTrue(rec_coding.details["is_thinking_to_coding"])
        self.assertEqual(detector.thinking_to_coding_count, 1)

        # Step 4: Final response complete (streaming stopped)
        snap_completed = DOMTelemetrySnapshot.from_dict({
            "is_streaming": False,
            "has_turn": True,
            "turn_chars": 1200,
            "thought": {"present": True, "active": False, "collapsed": True, "duration_seconds": 8.4},
            "code": {"present": True, "blocks_count": 1, "languages": ["python"], "total_code_chars": 900}
        })
        detector.ingest_snapshot(snap_completed)
        self.assertEqual(detector.current_state, SolState.COMPLETED)

        summary = detector.get_summary()
        self.assertEqual(summary["current_state"], "COMPLETED")
        self.assertEqual(summary["thinking_to_coding_transitions"], 1)
        self.assertEqual(len(transitions_observed), 4)

    def test_error_and_saturation_state_triggers(self):
        detector = SolStateTransitionDetector()
        
        # Test error detection
        snap_err = DOMTelemetrySnapshot.from_dict({
            "error": {"detected": True, "text": "Rate limit reached. Try again later."}
        })
        detector.ingest_snapshot(snap_err)
        self.assertEqual(detector.current_state, SolState.ERROR)

        # Test saturation detection via DOM
        detector2 = SolStateTransitionDetector()
        snap_sat = DOMTelemetrySnapshot.from_dict({
            "saturation": {
                "detected": True,
                "reasons": ["conversation is too long"],
                "input_disabled": True
            }
        })
        detector2.ingest_snapshot(snap_sat)
        self.assertEqual(detector2.current_state, SolState.SATURATED)

    # -------------------------------------------------------------------------
    # 3. Context Saturation Sentinel
    # -------------------------------------------------------------------------
    def test_context_saturation_sentinel_thresholds(self):
        thresholds = SaturationThresholds(
            max_turns=10,
            max_total_chars=10_000,
            estimated_token_limit=2_500,
            soft_warning_ratio=0.70,
            hard_saturation_ratio=0.90
        )
        sentinel = ContextSaturationSentinel(thresholds=thresholds)

        # Turn 1: Normal volume
        m1 = sentinel.record_turn(user_chars=500, assistant_chars=1000)
        self.assertEqual(m1["status"], "NORMAL")
        self.assertFalse(m1["handover_recommended"])

        # Multiple turns reaching soft warning
        for _ in range(5):
            sentinel.record_turn(user_chars=200, assistant_chars=800)
        
        should_handover, reason = sentinel.should_trigger_handover()
        self.assertFalse(should_handover)

        # Reach turn limit
        for _ in range(4):
            sentinel.record_turn(user_chars=200, assistant_chars=800)

        should_handover, reason = sentinel.should_trigger_handover()
        self.assertTrue(should_handover)
        self.assertIn("reached", reason.lower())

    # -------------------------------------------------------------------------
    # 4. Checkpoint Generation & Cryptographic Integrity
    # -------------------------------------------------------------------------
    def test_transition_checkpoint_generation_and_restoration(self):
        # Create temp files as artifacts
        src_dir = self.tmp_dir / "workspace"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "mod_a.py").write_text("class ModuleA: pass\n", encoding="utf-8")
        (src_dir / "mod_b.py").write_text("class ModuleB: pass\n", encoding="utf-8")

        test_proof = PassedTestEvidence(
            test_id="proof_01",
            suite_name="test_mod_a.py",
            duration_s=0.15,
            passed_assertions=12,
            failed_assertions=0,
            stdout_sha256=sha256_digest(b"12 passed in 0.15s")
        )

        engine = SolTelemetryEngine()
        chk = engine.create_checkpoint(
            aggregate_id="thread_test_42",
            sequence=3,
            goals={"mission": "Implement secure protocol", "strict_mode": True},
            plan=[{"step": 1, "done": True}, {"step": 2, "active": True}],
            tasks={"job_01": "SUCCEEDED"},
            messages=[{"role": "user", "intent": "Synthesize security layer"}],
            artifacts_dir=src_dir,
            approved_tests=[test_proof],
            prev_hash="1" * 64
        )

        # Invariant checks
        self.assertTrue(chk.checkpoint_id.startswith("chk_"))
        self.assertEqual(len(chk.state_root_hash), 64)
        self.assertEqual(len(chk.evidence_hash), 64)
        self.assertIn("mod_a.py", chk.A)
        self.assertIn("mod_b.py", chk.A)
        self.assertEqual(chk.approved_tests[0].verdict, "PASS")

        # Check serialization roundtrip
        chk_dict = chk.to_dict()
        restored = TransitionCheckpoint.from_dict(chk_dict)
        self.assertEqual(restored.checkpoint_id, chk.checkpoint_id)
        self.assertEqual(restored.state_root_hash, chk.state_root_hash)
        self.assertEqual(restored.evidence_hash, chk.evidence_hash)

        # Generate genesis prompt and verify contents
        genesis_prompt = chk.generate_genesis_prompt("Synthesize mod_c.py with AES-GCM")
        self.assertIn(":GOAL #transition_seed @Sol", genesis_prompt)
        self.assertIn(chk.state_root_hash, genesis_prompt)
        self.assertIn(chk.evidence_hash, genesis_prompt)
        self.assertIn("Synthesize mod_c.py with AES-GCM", genesis_prompt)

    # -------------------------------------------------------------------------
    # 5. Semantic Prompt Templates (SPECTER-DSL)
    # -------------------------------------------------------------------------
    def test_semantic_prompt_templates(self):
        engine = SolPromptEngine()

        # Synthesis
        prompt_syn = engine.render(SolPromptType.SYNTHESIS, {
            "goal_id": "g-101",
            "target_module": "specter_core.audit",
            "timeout_s": 90,
            "idempotency_key": "sec-audit-01",
            "min_assertions": 8,
            "intent_description": "Implement audit trail logging",
            "invariants_list": "- Deterministic SHA-256\n- Non-blocking sqlite",
            "input_contract": "event: dict",
            "output_contract": "event_hash: str"
        })
        self.assertIn(":GOAL #g-101 @Sol act=synthesize.module.v1", prompt_syn)
        self.assertIn("specter_core.audit", prompt_syn)

        # Refactor
        prompt_ref = engine.render(SolPromptType.REFACTOR, {
            "goal_id": "g-102",
            "target_file": "bridge_job_state.py",
            "target_symbol": "complete_job",
            "problem_description": "Timeouts occurred before lock release",
            "desired_behavior": "Release lock immediately in finally block"
        })
        self.assertIn(":GOAL #g-102 @Sol act=refactor.symbol.v1", prompt_ref)
        self.assertIn("complete_job", prompt_ref)

        # Adversarial Audit
        prompt_adv = engine.render(SolPromptType.ADVERSARIAL_AUDIT, {
            "goal_id": "g-103",
            "target_file": "mesh_loop.py",
            "code_content": "def run(): while True: pass"
        })
        self.assertIn(":GOAL #g-103 @Sol act=audit.falsify.v1", prompt_adv)
        self.assertIn("strat=skeptical_falsification", prompt_adv)

        # Missing variable error test
        with self.assertRaises(KeyError):
            engine.render(SolPromptType.SYNTHESIS, {"goal_id": "missing_others"})


if __name__ == "__main__":
    unittest.main()
