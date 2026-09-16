#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
TEST SUITE: SPECTER-DSL CANONICAL PROMPT CATALOG & DETERMINISTIC COMPILER
Architecture: Specter Sovereign Mesh / Astra Ecosystem
Target: Python 3.12+ (Windows 10, Zero External Dependencies)
Verification: Deterministic compilation, Token Economy (>65%), 3 Expansion Templates,
              Sovereign Handovers, and SQLite WAL Persistence.
================================================================================
"""

import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from specter_dsl_catalog import (
    canonical_json,
    sha256_digest,
    SovereignAgent,
    ExpansionPhase,
    SUPPORTED_OPCODES,
    SPECTER_JSONLD_CONTEXT,
    DSLFrame,
    DSLLexer,
    DSLCodec,
    DSLError,
    TokenEconomyMetrics,
    calculate_token_density,
    CompiledPrompt,
    SpecterPromptCompiler,
    TaskSynthesisTemplate,
    ReverseAuditTemplate,
    ContextCompactionTemplate,
    SovereignMeshEnvelope,
    create_expansion_handover,
    WALPromptStore,
    compile_task_synthesis,
    compile_reverse_audit,
    compile_context_compaction,
    run_benchmark
)


class TestDeterministicCompilation(unittest.TestCase):
    """Verifies RFC 8785 determinism, reproducible SHA-256 hashes, and round-trip fidelity."""

    def setUp(self):
        self.compiler = SpecterPromptCompiler()

    def test_reproducible_hash_and_dsl(self):
        params = {
            "module_name": "specter_context_compactor.py",
            "min_coverage": 90,
            "timeout_sec": 120
        }
        compiled_1 = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, params)
        compiled_2 = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, params)

        # Both compilations must be 100% byte-identical
        self.assertEqual(compiled_1.dsl_text, compiled_2.dsl_text)
        self.assertEqual(compiled_1.prompt_hash, compiled_2.prompt_hash)
        self.assertEqual(compiled_1.spec_hash, compiled_2.spec_hash)
        self.assertTrue(compiled_1.verify_integrity())
        self.assertTrue(compiled_2.verify_integrity())

    def test_parameter_order_insensitivity(self):
        params_a = {"module_name": "mod_a.py", "timeout_sec": 120, "min_coverage": 95}
        params_b = {"min_coverage": 95, "module_name": "mod_a.py", "timeout_sec": 120}

        comp_a = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, params_a)
        comp_b = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, params_b)

        self.assertEqual(comp_a.spec_hash, comp_b.spec_hash)
        self.assertEqual(comp_a.dsl_text, comp_b.dsl_text)
        self.assertEqual(comp_a.prompt_hash, comp_b.prompt_hash)

    def test_decompilation_and_roundtrip_equivalence(self):
        params = {"module_name": "specter_audit_core.py"}
        compiled = self.compiler.compile(ExpansionPhase.REVERSE_AUDIT, params)

        # Decompile back to typed frames
        frames = self.compiler.decompile(compiled.dsl_text)
        self.assertEqual(len(frames), 6)

        # Verify roundtrip determinism
        is_roundtrip_eq = self.compiler.verify_deterministic_roundtrip(compiled)
        self.assertTrue(is_roundtrip_eq)

    def test_json_ld_bidirectional_roundtrip(self):
        params = {"db_target": "storage/test_fabric.sqlite3"}
        compiled = self.compiler.compile(ExpansionPhase.CONTEXT_COMPACTION, params)

        # To JSON-LD and back
        json_ld = compiled.json_ld
        reconstructed = DSLCodec.from_json_ld(json_ld)
        self.assertEqual(len(reconstructed), len(compiled.frames))

        # Check opcodes preserved
        orig_opcodes = [f.opcode for f in compiled.frames]
        re_opcodes = [f.opcode for f in reconstructed]
        self.assertEqual(orig_opcodes, re_opcodes)


class TestTokenEconomySavings(unittest.TestCase):
    """
    Formally verifies token economy metrics and proves that token reduction
    meets or exceeds the required >65% threshold across all 3 canonical expansion phases.
    """

    def setUp(self):
        self.compiler = SpecterPromptCompiler()

    def test_task_synthesis_token_savings_exceeds_65_percent(self):
        compiled = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, {
            "module_name": "specter_context_compactor.py"
        })
        metrics = compiled.metrics

        # Must strictly meet threshold (>65%)
        self.assertTrue(metrics.meets_threshold(65.0))
        self.assertGreaterEqual(metrics.token_reduction_vs_jsonld_pct, 65.0)
        self.assertGreater(metrics.compression_ratio, 1.8)

        # Verify token counts are positive and ordered correctly
        self.assertLess(metrics.dsl_tokens_est, metrics.json_tokens_est)
        self.assertLess(metrics.json_tokens_est, metrics.json_ld_tokens_est)

    def test_reverse_audit_token_savings_exceeds_65_percent(self):
        compiled = self.compiler.compile(ExpansionPhase.REVERSE_AUDIT, {
            "module_name": "specter_context_compactor.py"
        })
        metrics = compiled.metrics

        self.assertTrue(metrics.meets_threshold(65.0))
        self.assertGreaterEqual(metrics.token_reduction_vs_jsonld_pct, 65.0)
        self.assertGreater(metrics.compression_ratio, 1.8)

    def test_context_compaction_token_savings_exceeds_65_percent(self):
        compiled = self.compiler.compile(ExpansionPhase.CONTEXT_COMPACTION, {
            "db_target": "storage/specter_fabric.sqlite3"
        })
        metrics = compiled.metrics

        self.assertTrue(metrics.meets_threshold(65.0))
        self.assertGreaterEqual(metrics.token_reduction_vs_jsonld_pct, 65.0)
        self.assertGreater(metrics.compression_ratio, 1.8)

    def test_benchmark_suite_all_pass(self):
        results = run_benchmark()
        self.assertEqual(len(results), 3)
        for phase, data in results.items():
            self.assertTrue(data["meets_65pct"], f"Phase {phase} failed to meet >65% token savings: {data}")
            self.assertGreaterEqual(data["savings_vs_jsonld_pct"], 65.0)


class TestCanonicalExpansionTemplates(unittest.TestCase):
    """Verifies structure, micro-opcodes, and semantic attributes of the 3 templates."""

    def setUp(self):
        self.compiler = SpecterPromptCompiler()

    def test_task_synthesis_template(self):
        compiled = compile_task_synthesis("specter_test_mod.py", actor="Astra", min_coverage=95)
        frames = compiled.frames

        # Must have exactly 6 canonical frames
        self.assertEqual(len(frames), 6)
        opcodes = [f.opcode for f in frames]
        self.assertEqual(opcodes, ["GOAL", "PLAN", "EXEC", "VERIFY", "ATTAINED", "MEM"])

        # 1. :GOAL
        goal = frames[0]
        self.assertEqual(goal.actor, "Astra")
        self.assertEqual(goal.get("act"), "module.synthesize.v1")
        self.assertEqual(goal.get("target"), "specter_test_mod.py")

        # 2. :PLAN
        plan = frames[1]
        self.assertEqual(plan.get("strat"), "atomic_fsync")
        self.assertEqual(plan.get("stdlib_only"), True)
        self.assertEqual(plan.get("strict_typing"), True)

        # 3. :EXEC
        exec_f = frames[2]
        self.assertEqual(exec_f.get("worker"), "astra_worker")
        self.assertEqual(exec_f.get("attempt"), 1)

        # 4. :VERIFY
        verify = frames[3]
        self.assertEqual(verify.get("verifier"), "python_unittest_runner")
        self.assertEqual(verify.get("pass_pct"), 100)

        # 5. :ATTAINED
        attained = frames[4]
        self.assertEqual(attained.get("status"), "SUCCESS")
        self.assertEqual(attained.get("min_cov"), 95)

        # 6. :MEM
        mem = frames[5]
        self.assertEqual(mem.actor, "Astra")
        self.assertEqual(mem.get("domain"), "specter.engineering")
        self.assertEqual(mem.get("pred"), "ESTABLISHES")
        self.assertEqual(mem.get("conf"), 1.0)

    def test_reverse_audit_template(self):
        compiled = compile_reverse_audit("specter_test_mod.py", actor="Sol", scope="concurrency_resilience")
        frames = compiled.frames

        self.assertEqual(len(frames), 6)
        opcodes = [f.opcode for f in frames]
        self.assertEqual(opcodes, ["GOAL", "PLAN", "EXEC", "VERIFY", "ATTAINED", "MEM"])

        goal = frames[0]
        self.assertEqual(goal.actor, "Sol")
        self.assertEqual(goal.get("act"), "code.reverse_audit.v1")

        plan = frames[1]
        self.assertEqual(plan.get("strat"), "skeptical_falsification")

        exec_f = frames[2]
        self.assertEqual(exec_f.get("worker"), "sol_reasoner")
        self.assertEqual(exec_f.get("mode"), "adversarial_deep")

        verify = frames[3]
        self.assertEqual(verify.get("verifier"), "formal_invariant_checker")
        self.assertEqual(verify.get("verdict"), "AUDIT_PASS")

        attained = frames[4]
        self.assertEqual(attained.get("status"), "VERIFIED")
        self.assertEqual(attained.get("grade"), "A_PLUS")

        mem = frames[5]
        self.assertEqual(mem.actor, "Sol")
        self.assertEqual(mem.get("domain"), "specter.security")
        self.assertEqual(mem.get("pred"), "VERIFIED_AGAINST")

    def test_context_compaction_template(self):
        compiled = compile_context_compaction(
            db_target="storage/specter_fabric.sqlite3",
            actor="Kimi",
            before=10000,
            after=2500
        )
        frames = compiled.frames

        self.assertEqual(len(frames), 6)
        opcodes = [f.opcode for f in frames]
        self.assertEqual(opcodes, ["GOAL", "PLAN", "EXEC", "VERIFY", "ATTAINED", "MEM"])

        goal = frames[0]
        self.assertEqual(goal.actor, "Kimi")
        self.assertEqual(goal.get("act"), "context.compact_handover.v1")
        self.assertEqual(goal.get("vec"), "S_t")

        plan = frames[1]
        self.assertEqual(plan.get("strat"), "lossless_extractive")

        exec_f = frames[2]
        self.assertEqual(exec_f.get("worker"), "kimi_sentinel")
        self.assertEqual(exec_f.get("tier"), "WAL_TIER_1")

        verify = frames[3]
        self.assertEqual(verify.get("verifier"), "wal_roundtrip_verifier")
        self.assertEqual(verify.get("roundtrip_hash_eq"), True)

        attained = frames[4]
        self.assertEqual(attained.get("status"), "CONSOLIDATED")
        self.assertEqual(attained.get("comp"), 75.0)

        mem = frames[5]
        self.assertEqual(mem.actor, "Kimi")
        self.assertEqual(mem.get("domain"), "specter.persistence")
        self.assertEqual(mem.get("pred"), "PRESERVED_IN_WAL")


class TestSovereignMeshOrchestration(unittest.TestCase):
    """Verifies multi-agent handover transitions and dispatch envelopes."""

    def test_sovereign_agents_attributes(self):
        self.assertEqual(SovereignAgent.ANTIGRAVITY.tag, "@Antigravity")
        self.assertEqual(SovereignAgent.KIMI.tag, "@Kimi")
        self.assertEqual(SovereignAgent.ASTRA.tag, "@Astra")
        self.assertEqual(SovereignAgent.SOL.tag, "@Sol")

        self.assertIn("Orchestrator", SovereignAgent.ANTIGRAVITY.role_description)
        self.assertIn("Sentinel", SovereignAgent.KIMI.role_description)
        self.assertIn("Tooling", SovereignAgent.ASTRA.role_description)
        self.assertIn("Auditor", SovereignAgent.SOL.role_description)

    def test_expansion_handover_transition_chain(self):
        # Step 1: Antigravity -> Astra for TASK_SYNTHESIS
        env_1 = create_expansion_handover(
            from_agent=SovereignAgent.ANTIGRAVITY,
            to_agent=SovereignAgent.ASTRA,
            phase=ExpansionPhase.TASK_SYNTHESIS,
            params={"module_name": "specter_module.py"}
        )
        self.assertTrue(env_1.envelope_id.startswith("env_"))
        self.assertEqual(env_1.source_agent, SovereignAgent.ANTIGRAVITY)
        self.assertEqual(env_1.target_agent, SovereignAgent.ASTRA)
        self.assertEqual(len(env_1.handover_hash), 64)

        # Step 2: Astra -> Sol for REVERSE_AUDIT
        env_2 = create_expansion_handover(
            from_agent=SovereignAgent.ASTRA,
            to_agent=SovereignAgent.SOL,
            phase=ExpansionPhase.REVERSE_AUDIT,
            params={"module_name": "specter_module.py", "proof": env_1.compiled_prompt.prompt_hash}
        )
        self.assertEqual(env_2.source_agent, SovereignAgent.ASTRA)
        self.assertEqual(env_2.target_agent, SovereignAgent.SOL)

        # Step 3: Sol -> Kimi for CONTEXT_COMPACTION
        env_3 = create_expansion_handover(
            from_agent=SovereignAgent.SOL,
            to_agent=SovereignAgent.KIMI,
            phase=ExpansionPhase.CONTEXT_COMPACTION,
            params={"proof": env_2.compiled_prompt.prompt_hash}
        )
        self.assertEqual(env_3.source_agent, SovereignAgent.SOL)
        self.assertEqual(env_3.target_agent, SovereignAgent.KIMI)


class TestSQLiteWALPersistence(unittest.TestCase):
    """Verifies durable persistence in SQLite WAL mode."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_fabric.sqlite3"
        self.store = WALPromptStore(self.db_path)
        self.compiler = SpecterPromptCompiler()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sqlite_wal_pragmas(self):
        with self.store.connection() as con:
            journal_mode = con.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

    def test_persist_and_retrieve_prompt(self):
        compiled = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, {
            "module_name": "persisted_module.py"
        })
        saved_hash = self.store.persist(compiled)
        self.assertEqual(saved_hash, compiled.prompt_hash)

        retrieved = self.store.get_prompt(compiled.prompt_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["prompt_id"], compiled.prompt_id)
        self.assertEqual(retrieved["phase"], "TASK_SYNTHESIS")
        self.assertEqual(retrieved["actor"], "Astra")
        self.assertEqual(retrieved["prompt_hash"], compiled.prompt_hash)

    def test_persist_handover_envelope(self):
        env = create_expansion_handover(
            from_agent=SovereignAgent.ANTIGRAVITY,
            to_agent=SovereignAgent.SOL,
            phase=ExpansionPhase.REVERSE_AUDIT,
            params={"module_name": "secure_module.py"},
            compiler=self.compiler
        )
        h_hash = self.store.persist_handover(env)
        self.assertEqual(h_hash, env.handover_hash)

        # Check prompt entry also exists
        prompt_entry = self.store.get_prompt(env.compiled_prompt.prompt_id)
        self.assertIsNotNone(prompt_entry)

    def test_list_prompts_by_phase(self):
        p1 = self.compiler.compile(ExpansionPhase.TASK_SYNTHESIS, {"module_name": "m1.py"})
        p2 = self.compiler.compile(ExpansionPhase.REVERSE_AUDIT, {"module_name": "m2.py"})
        self.store.persist(p1)
        self.store.persist(p2)

        all_prompts = self.store.list_prompts()
        self.assertEqual(len(all_prompts), 2)

        syn_prompts = self.store.list_prompts(ExpansionPhase.TASK_SYNTHESIS)
        self.assertEqual(len(syn_prompts), 1)
        self.assertEqual(syn_prompts[0]["module_name" if "module_name" in syn_prompts[0] else "phase"], "TASK_SYNTHESIS")


class TestValidationAndErrorHandling(unittest.TestCase):
    """Verifies invariant validation and error handling."""

    def setUp(self):
        self.compiler = SpecterPromptCompiler()

    def test_empty_frames_error(self):
        with self.assertRaises(DSLError):
            self.compiler.validate_frames([])

    def test_missing_goal_first_error(self):
        invalid_frames = [
            DSLFrame(opcode="PLAN", params={"step": ["s1"]}),
            DSLFrame(opcode="EXEC", params={"worker": "w1"})
        ]
        with self.assertRaises(DSLError):
            self.compiler.validate_frames(invalid_frames)

    def test_missing_core_opcode_error(self):
        # Missing :VERIFY and :ATTAINED
        incomplete_frames = [
            DSLFrame(opcode="GOAL", id="t1", actor="Astra"),
            DSLFrame(opcode="PLAN", params={"step": ["s1"]}),
            DSLFrame(opcode="EXEC", params={"worker": "w1"})
        ]
        with self.assertRaises(DSLError):
            self.compiler.validate_frames(incomplete_frames)

    def test_malformed_line_error(self):
        with self.assertRaises(DSLError):
            DSLCodec.parse_line("GOAL without colon prefix")

    def test_dsl_value_escaping(self):
        frame = DSLFrame(
            opcode="MEM",
            params={"content": 'Special line\nwith "quotes" and \\backslashes'}
        )
        dsl_str = frame.to_dsl()
        parsed = DSLCodec.parse_line(dsl_str)
        self.assertEqual(parsed.get("content"), 'Special line\nwith "quotes" and \\backslashes')


if __name__ == "__main__":
    unittest.main()
