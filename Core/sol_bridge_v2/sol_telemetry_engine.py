# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER CORE // SOL TELEMETRY & OBSERVABILITY ENGINE (v2.0)
Architecture: ChatGPT Thinking / Coding Telemetry via Local Browser Control
Target: Sol (gpt-5-6-thinking / o1 / o3 / o4-mini / ChatGPT Extended Thinking)
Transport: Windows Named Pipe (\\\\.\\pipe\\codex-browser-use\\*) & Safe DOM Eval
Sovereign Principle: Cognition != Execution != Authority != Memory
================================================================================
Invariants:
1. Zero Private Token Leakage:
   - Does NOT read cookies, session storage, or authorization JWT tokens.
   - Evaluates purely structural UI presentation elements in the page DOM.
2. Zero Unsafe CDP / Network Exposure:
   - Communicates exclusively via local Windows Named Pipe IPC.
   - Zero open external debug ports, zero remote proxy dependencies.
3. Deterministic Evidence & Cryptographic Integrity:
   - State checkpoint conforms to S_t = (G, P, T, M, A, X, C).
   - RFC 8785 deterministic canonical JSON encoding and SHA-256 evidence chains.
4. High Semantic Density Dialect (SPECTER-DSL):
   - Micro-opcodes (:GOAL, :PLAN, :EXEC, :VERIFY, :ATTAINED, :MEM).
   - Token-compressed engineering prompts with strict contract enforcement.
================================================================================
"""

import os
import sys
import time
import json
import uuid
import re
import hashlib
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple, Callable

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Optional import of CodexBrowserClient from local environment
try:
    from codex_browser_bridge import CodexBrowserClient
except ImportError:
    CLIENT_DIR = Path(os.environ.get("USERPROFILE", Path.home())) / ".hermes" / "codex_browser_bridge"
    if CLIENT_DIR.is_dir() and str(CLIENT_DIR) not in sys.path:
        sys.path.insert(0, str(CLIENT_DIR))
    try:
        from client import CodexBrowserClient
    except ImportError:
        CodexBrowserClient = None


# ==============================================================================
# CRYPTOGRAPHIC & SERIALIZATION PRIMITIVES
# ==============================================================================

def canonical_json(obj: Any) -> bytes:
    """
    RFC 8785 compliant canonical deterministic JSON encoding.
    Enforces sorted keys, compact separators, ASCII representation, and no NaN.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=True,
        separators=(',', ':'),
        allow_nan=False
    ).encode('ascii')


def sha256_digest(data: bytes) -> str:
    """Computes standard 64-character lowercase hex SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of an on-disk file in chunks."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


# ==============================================================================
# PART 1: DOM JAVASCRIPT TELEMETRY PAYLOAD (ZERO-TOKEN / ZERO-LEAK)
# ==============================================================================

DOM_TELEMETRY_JS = r"""
(() => {
    // SPECTER DOM OBSERVER: Pure structural inspection.
    // ZERO token scraping, ZERO cookie extraction, ZERO external network calls.
    try {
        // 1. Stream & Execution Controls
        const stopBtn = document.querySelector(
            'button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="Parar"], button[aria-label*="Cancelar"]'
        );
        const isStreaming = !!stopBtn;

        // 2. Assistant Turns Discovery
        const assistantTurns = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
        const lastTurn = assistantTurns.length > 0 ? assistantTurns[assistantTurns.length - 1] : null;
        const hasTurn = !!lastTurn;

        let turnText = '';
        let turnHtmlLen = 0;
        if (lastTurn) {
            turnText = lastTurn.innerText || '';
            turnHtmlLen = (lastTurn.innerHTML || '').length;
        }

        // 3. Extended Thinking / Reasoning Inspection
        let thoughtPresent = false;
        let thoughtActive = false;
        let thoughtCollapsed = false;
        let thoughtDurationText = '';
        let thoughtDurationSec = 0.0;
        let thoughtTextLen = 0;

        if (lastTurn) {
            // Check collapsible thought / reasoning containers
            const thoughtContainers = Array.from(lastTurn.querySelectorAll(
                '[data-testid*="thought"], [data-testid*="reasoning"], [class*="thought"], [class*="reasoning"], summary, details'
            ));
            
            // Check button with thinking label
            const thinkingBtn = Array.from(lastTurn.querySelectorAll('button')).find(b => {
                const label = (b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '');
                return /thought|pensou|thinking|pensando|racioc/i.test(label);
            });

            if (thoughtContainers.length > 0 || thinkingBtn) {
                thoughtPresent = true;
                const activeEl = thinkingBtn || thoughtContainers[0];
                const rawText = activeEl.innerText || activeEl.getAttribute('aria-label') || '';
                thoughtTextLen = rawText.length;

                // Match durations e.g., "Thought for 12 seconds", "Pensou por 5s", "Thought for 3s"
                const matchSec = rawText.match(/(?:thought for|pensou por|thinking for|racioc[íi]nio por)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:s|segundos|seconds)?/i);
                if (matchSec) {
                    thoughtDurationSec = parseFloat(matchSec[1]);
                    thoughtDurationText = matchSec[0].trim();
                }

                // If stop button is visible, but no final response content exists yet, thinking is actively happening
                // If it states "thinking..." or has spinner, it is active
                const hasSpinner = !!activeEl.querySelector('svg.animate-spin, [class*="spin"], [data-testid*="spinner"]');
                const isThinkingText = /pensando|thinking|raciocinando/i.test(rawText);

                if (isStreaming && (hasSpinner || isThinkingText || thoughtDurationSec === 0.0)) {
                    thoughtActive = true;
                    thoughtCollapsed = false;
                } else {
                    thoughtActive = false;
                    thoughtCollapsed = true;
                }
            }
        }

        // 4. Code Blocks Inspection
        let codePresent = false;
        let codeBlocksCount = 0;
        let detectedLanguages = [];
        let totalCodeChars = 0;
        let firstBlockPreview = '';

        if (lastTurn) {
            const preBlocks = Array.from(lastTurn.querySelectorAll('pre'));
            codeBlocksCount = preBlocks.length;
            codePresent = codeBlocksCount > 0;

            preBlocks.forEach((pre, idx) => {
                const code = pre.querySelector('code');
                const txt = (code ? code.innerText : pre.innerText) || '';
                totalCodeChars += txt.length;
                if (idx === 0) {
                    firstBlockPreview = txt.slice(0, 100);
                }

                // Language detection from class, header divs, or labels
                let lang = '';
                if (code && code.className) {
                    const match = code.className.match(/language-([a-zA-Z0-9_-]+)/);
                    if (match) lang = match[1].toLowerCase();
                }
                if (!lang) {
                    // Look at header div inside pre wrapper
                    const header = pre.parentElement ? pre.parentElement.querySelector('[class*="header"], [class*="language"]') : null;
                    if (header && header.innerText) {
                        lang = header.innerText.split('\\n')[0].trim().toLowerCase();
                    }
                }
                if (!lang) {
                    // Check previous sibling or inner text labels (e.g. "Python\\nRun")
                    const lines = txt.split('\\n');
                    if (lines[0] && lines[0].length < 20 && /python|javascript|typescript|json|bash|sh|sql|html|css|yaml/i.test(lines[0])) {
                        lang = lines[0].trim().toLowerCase();
                    }
                }
                if (lang && !detectedLanguages.includes(lang)) {
                    detectedLanguages.push(lang);
                }
            });
        }

        // 5. Context Saturation & System Warnings Inspection
        let saturationDetected = false;
        let saturationReasons = [];
        let inputDisabled = false;

        const composer = document.querySelector('#prompt-textarea');
        if (composer) {
            if (composer.disabled || composer.getAttribute('contenteditable') === 'false') {
                inputDisabled = true;
            }
        }

        // Check DOM alert banners and warning toasts
        const alertElements = Array.from(document.querySelectorAll(
            '[role="alert"], [class*="banner"], [class*="toast"], [class*="alert"], [data-testid*="error"]'
        ));

        const bodyText = document.body ? document.body.innerText : '';
        const saturationRegexes = [
            /conversation is too long/i,
            /conversa [ée] muito longa/i,
            /inicie uma nova conversa/i,
            /start a new conversation/i,
            /message cap reached/i,
            /length limit reached/i,
            /limite de tamanho atingido/i,
            /context window/i
        ];

        for (const r of saturationRegexes) {
            if (r.test(bodyText)) {
                saturationDetected = true;
                saturationReasons.push(r.source);
            }
        }

        // 6. Error & Interruption Detection
        let errorDetected = false;
        let errorText = '';
        const errorRegexes = [
            /there was an error generating a response/i,
            /ocorreu um erro ao gerar/i,
            /something went wrong/i,
            /rate limit reached/i,
            /too many requests/i,
            /limite de solicita[çc][õo]es/i
        ];

        for (const el of alertElements) {
            const elText = el.innerText || '';
            for (const er of errorRegexes) {
                if (er.test(elText)) {
                    errorDetected = true;
                    errorText = elText.slice(0, 200).trim();
                    break;
                }
            }
            if (errorDetected) break;
        }

        // 7. Active Model Slug Discovery
        let modelSlug = '';
        const slugEl = document.querySelector('[data-message-model-slug]');
        if (slugEl) {
            modelSlug = slugEl.getAttribute('data-message-model-slug') || '';
        }

        return {
            success: true,
            timestamp_ms: Date.now(),
            is_streaming: isStreaming,
            has_turn: hasTurn,
            turn_chars: turnText.length,
            turn_html_len: turnHtmlLen,
            thought: {
                present: thoughtPresent,
                active: thoughtActive,
                collapsed: thoughtCollapsed,
                text_length: thoughtTextLen,
                duration_text: thoughtDurationText,
                duration_seconds: thoughtDurationSec
            },
            code: {
                present: codePresent,
                blocks_count: codeBlocksCount,
                languages: detectedLanguages,
                total_code_chars: totalCodeChars,
                first_block_preview: firstBlockPreview
            },
            saturation: {
                detected: saturationDetected,
                reasons: saturationReasons,
                input_disabled: inputDisabled
            },
            error: {
                detected: errorDetected,
                text: errorText
            },
            model_slug: modelSlug
        };
    } catch(err) {
        return {
            success: false,
            error: String(err),
            timestamp_ms: Date.now()
        };
    }
})()
""".strip()


# ==============================================================================
# PART 2: STATE MACHINE & TRANSITION DETECTOR
# ==============================================================================

class SolState(str, Enum):
    IDLE = "IDLE"
    SUBMITTED = "SUBMITTED"
    THINKING = "THINKING"
    CODING = "CODING"
    STREAMING_TEXT = "STREAMING_TEXT"
    COMPLETED = "COMPLETED"
    SATURATED = "SATURATED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class DOMTelemetrySnapshot:
    timestamp_ms: float
    is_streaming: bool
    has_turn: bool
    turn_chars: int
    thought_present: bool
    thought_active: bool
    thought_collapsed: bool
    thought_duration_seconds: float
    code_present: bool
    code_blocks_count: int
    code_languages: List[str]
    total_code_chars: int
    first_block_preview: str
    saturation_detected: bool
    saturation_reasons: List[str]
    input_disabled: bool
    error_detected: bool
    error_text: str
    model_slug: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DOMTelemetrySnapshot':
        t = data.get("thought", {})
        c = data.get("code", {})
        s = data.get("saturation", {})
        e = data.get("error", {})
        return cls(
            timestamp_ms=float(data.get("timestamp_ms", time.time() * 1000.0)),
            is_streaming=bool(data.get("is_streaming", False)),
            has_turn=bool(data.get("has_turn", False)),
            turn_chars=int(data.get("turn_chars", 0)),
            thought_present=bool(t.get("present", False)),
            thought_active=bool(t.get("active", False)),
            thought_collapsed=bool(t.get("collapsed", False)),
            thought_duration_seconds=float(t.get("duration_seconds", 0.0)),
            code_present=bool(c.get("present", False)),
            code_blocks_count=int(c.get("blocks_count", 0)),
            code_languages=list(c.get("languages", [])),
            total_code_chars=int(c.get("total_code_chars", 0)),
            first_block_preview=str(c.get("first_block_preview", "")),
            saturation_detected=bool(s.get("detected", False)),
            saturation_reasons=list(s.get("reasons", [])),
            input_disabled=bool(s.get("input_disabled", False)),
            error_detected=bool(e.get("detected", False)),
            error_text=str(e.get("text", "")),
            model_slug=str(data.get("model_slug", ""))
        )


@dataclass
class TransitionRecord:
    from_state: SolState
    to_state: SolState
    timestamp_s: float
    duration_in_prev_state_s: float
    details: Dict[str, Any]
    transition_hash: str


class SolStateTransitionDetector:
    """
    State machine and edge-triggered event detector for Sol's lifecycle.
    Specifically captures the instant of THINKING -> CODING transition without
    requiring private tokens, invasive hooks, or network telemetry.
    """

    def __init__(self, on_transition: Optional[Callable[[TransitionRecord], None]] = None):
        self.current_state: SolState = SolState.IDLE
        self.state_enter_time: float = time.time()
        self.history: List[TransitionRecord] = []
        self.on_transition_callback = on_transition
        self.last_snapshot: Optional[DOMTelemetrySnapshot] = None

        # Cumulative telemetry metrics
        self.total_thinking_time_s: float = 0.0
        self.total_coding_time_s: float = 0.0
        self.thinking_to_coding_count: int = 0

    def evaluate_state(self, snap: DOMTelemetrySnapshot) -> SolState:
        """Determines the current discrete state from a telemetry snapshot."""
        if snap.error_detected:
            return SolState.ERROR
        if snap.saturation_detected or snap.input_disabled:
            return SolState.SATURATED

        if not snap.has_turn:
            if snap.is_streaming:
                return SolState.SUBMITTED
            return SolState.IDLE

        if snap.is_streaming:
            # If thought is active OR (thought is present and not yet collapsed and no code exists)
            if snap.thought_active or (snap.thought_present and not snap.thought_collapsed and not snap.code_present):
                return SolState.THINKING
            # If code blocks are actively being streamed
            if snap.code_present and snap.code_blocks_count > 0:
                return SolState.CODING
            # Otherwise, streaming general markdown text
            return SolState.STREAMING_TEXT

        # Generation finished (not streaming and has turn)
        return SolState.COMPLETED

    def ingest_snapshot(self, snapshot: DOMTelemetrySnapshot) -> Optional[TransitionRecord]:
        """
        Ingests a fresh DOM snapshot, computes state changes, updates durations,
        and triggers callback upon transition (especially THINKING -> CODING).
        """
        now = time.time()
        new_state = self.evaluate_state(snapshot)
        transition_record: Optional[TransitionRecord] = None

        if new_state != self.current_state:
            duration_in_prev = max(0.0, now - self.state_enter_time)

            # Accumulate specific state durations
            if self.current_state == SolState.THINKING:
                self.total_thinking_time_s += duration_in_prev
            elif self.current_state == SolState.CODING:
                self.total_coding_time_s += duration_in_prev

            # Check if this is the critical Thinking -> Coding transition
            is_thinking_to_coding = (self.current_state == SolState.THINKING and new_state == SolState.CODING)
            if is_thinking_to_coding:
                self.thinking_to_coding_count += 1

            details = {
                "turn_chars": snapshot.turn_chars,
                "code_blocks_count": snapshot.code_blocks_count,
                "languages": snapshot.code_languages,
                "thought_duration_reported": snapshot.thought_duration_seconds,
                "is_thinking_to_coding": is_thinking_to_coding,
                "model_slug": snapshot.model_slug
            }

            # Generate deterministic hash for the transition event
            record_payload = {
                "from": self.current_state.value,
                "to": new_state.value,
                "ts": now,
                "duration_prev": round(duration_in_prev, 4),
                "details": details
            }
            ev_hash = sha256_digest(canonical_json(record_payload))

            transition_record = TransitionRecord(
                from_state=self.current_state,
                to_state=new_state,
                timestamp_s=now,
                duration_in_prev_state_s=duration_in_prev,
                details=details,
                transition_hash=ev_hash
            )

            self.history.append(transition_record)
            self.current_state = new_state
            self.state_enter_time = now

            if self.on_transition_callback:
                try:
                    self.on_transition_callback(transition_record)
                except Exception as cb_err:
                    print(f"[!] Warning: Transition callback failed: {cb_err}", file=sys.stderr)

        self.last_snapshot = snapshot
        return transition_record

    def get_summary(self) -> Dict[str, Any]:
        """Returns consolidated metrics of the observation session."""
        now = time.time()
        curr_duration = max(0.0, now - self.state_enter_time)
        return {
            "current_state": self.current_state.value,
            "current_state_duration_s": round(curr_duration, 2),
            "total_thinking_time_s": round(self.total_thinking_time_s, 2),
            "total_coding_time_s": round(self.total_coding_time_s, 2),
            "thinking_to_coding_transitions": self.thinking_to_coding_count,
            "transitions_total": len(self.history),
            "last_event_hash": self.history[-1].transition_hash if self.history else None
        }


# ==============================================================================
# PART 3: CONTEXT SATURATION & CHECKPOINT ENGINE
# ==============================================================================

@dataclass
class SaturationThresholds:
    """Thresholds for proactive context handovers."""
    max_turns: int = 40
    max_total_chars: int = 350_000
    estimated_token_limit: int = 90_000
    soft_warning_ratio: float = 0.75
    hard_saturation_ratio: float = 0.90


@dataclass
class PassedTestEvidence:
    """Cryptographic evidence of an approved verification suite."""
    test_id: str
    suite_name: str
    duration_s: float
    passed_assertions: int
    failed_assertions: int
    stdout_sha256: str
    verdict: str = "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransitionCheckpoint:
    """
    S_t = (G, P, T, M, A, X, C) State Vector Checkpoint.
    Formulated according to Specter Core Event Sourcing Invariants.
    """
    checkpoint_id: str
    aggregate_id: str
    sequence: int
    created_at: float
    G: Dict[str, Any]  # Goals, restrictions, acceptance criteria
    P: List[Dict[str, Any]]  # Plan steps
    T: Dict[str, Any]  # Tasks and leases
    M: List[Dict[str, Any]]  # Distilled messages / knowledge
    A: Dict[str, str]  # Artifacts: filepath -> SHA-256
    X: Dict[str, Any]  # External effects
    C: Dict[str, Any]  # Configuration
    approved_tests: List[PassedTestEvidence]
    previous_checkpoint_hash: str
    state_root_hash: str = ""
    evidence_hash: str = ""

    def compute_state_root(self) -> str:
        state_dict = {
            "G": self.G,
            "P": self.P,
            "T": self.T,
            "M": self.M,
            "A": self.A,
            "X": self.X,
            "C": self.C,
            "sequence": self.sequence
        }
        return sha256_digest(canonical_json(state_dict))

    def compute_evidence_hash(self) -> str:
        tests_data = [t.to_dict() for t in self.approved_tests]
        pack = {
            "checkpoint_id": self.checkpoint_id,
            "aggregate_id": self.aggregate_id,
            "sequence": self.sequence,
            "created_at": round(self.created_at, 4),
            "state_root_hash": self.state_root_hash,
            "previous_hash": self.previous_checkpoint_hash,
            "tests": tests_data
        }
        return sha256_digest(canonical_json(pack))

    def finalize(self):
        self.state_root_hash = self.compute_state_root()
        self.evidence_hash = self.compute_evidence_hash()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "aggregate_id": self.aggregate_id,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "state_root_hash": self.state_root_hash,
            "evidence_hash": self.evidence_hash,
            "previous_checkpoint_hash": self.previous_checkpoint_hash,
            "approved_tests": [t.to_dict() for t in self.approved_tests],
            "artifacts_manifest": self.A,
            "state_vector": {
                "G": self.G,
                "P": self.P,
                "T": self.T,
                "M": self.M,
                "A": self.A,
                "X": self.X,
                "C": self.C
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TransitionCheckpoint':
        sv = data.get("state_vector", {})
        tests = [PassedTestEvidence(**t) for t in data.get("approved_tests", [])]
        inst = cls(
            checkpoint_id=data["checkpoint_id"],
            aggregate_id=data["aggregate_id"],
            sequence=data["sequence"],
            created_at=data["created_at"],
            G=sv.get("G", {}),
            P=sv.get("P", []),
            T=sv.get("T", {}),
            M=sv.get("M", []),
            A=sv.get("A", data.get("artifacts_manifest", {})),
            X=sv.get("X", {}),
            C=sv.get("C", {}),
            approved_tests=tests,
            previous_checkpoint_hash=data.get("previous_checkpoint_hash", "0" * 64),
            state_root_hash=data.get("state_root_hash", ""),
            evidence_hash=data.get("evidence_hash", "")
        )
        if not inst.state_root_hash:
            inst.finalize()
        return inst

    def generate_genesis_prompt(self, next_intent: str) -> str:
        """
        Synthesizes an ultra-dense SPECTER-DSL Genesis Prompt to reseed
        a brand new conversation thread with 100% state rehydration.
        """
        tests_summary = "\n".join([
            f"  - [{t.verdict}] {t.suite_name} (Assertions: {t.passed_assertions}, Output Hash: {t.stdout_sha256[:16]}...)"
            for t in self.approved_tests
        ]) or "  - No external tests registered."

        artifacts_summary = "\n".join([
            f"  - {path}: sha256={h[:16]}..."
            for path, h in list(self.A.items())[:12]
        ]) or "  - Zero file artifacts."

        prompt = f"""# SPECTER STATE HANDOVER // GENESIS THREAD SEED
:GOAL #transition_seed @Sol act=state_rehydrate.v1 aggregate={self.aggregate_id} seq={self.sequence}
:VERIFY state_root={self.state_root_hash} evidence={self.evidence_hash}
:MEM domain=specter_checkpoint checkpoint_id={self.checkpoint_id}

## 1. INVARIANT STATE VECTOR S_{self.sequence}
- **Active Goals (G)**: {json.dumps(self.G, ensure_ascii=False)}
- **Executed Plan (P)**: Completed {len(self.P)} steps.
- **Approved Tests Evidence**:
{tests_summary}
- **Validated Artifacts Manifest (A)**:
{artifacts_summary}

## 2. SOVEREIGN DIRECTIVE
- Cognition != Execution != Authority != Memory.
- Maintain zero hallucinations, absolute typing, and RFC 8785 canonical determinism.

## 3. NEXT OBJECTIVE (:EXEC)
{next_intent}
"""
        return prompt.strip()


class ContextSaturationSentinel:
    """
    Monitors cumulative conversation metrics across turns to detect
    context exhaustion, issuing early warnings and triggering transition payloads.
    """

    def __init__(self, thresholds: Optional[SaturationThresholds] = None):
        self.thresholds = thresholds or SaturationThresholds()
        self.turns_count: int = 0
        self.total_chars: int = 0
        self.last_warning_issued: Optional[str] = None

    def record_turn(self, user_chars: int, assistant_chars: int) -> Dict[str, Any]:
        """Records characters for a turn and evaluates saturation."""
        self.turns_count += 1
        turn_total = user_chars + assistant_chars
        self.total_chars += turn_total

        est_tokens = int(self.total_chars / 3.7)
        token_ratio = min(1.0, est_tokens / max(1, self.thresholds.estimated_token_limit))
        turn_ratio = min(1.0, self.turns_count / max(1, self.thresholds.max_turns))

        is_critical = (
            token_ratio >= self.thresholds.hard_saturation_ratio or
            turn_ratio >= self.thresholds.hard_saturation_ratio or
            self.total_chars >= self.thresholds.max_total_chars
        )
        is_warning = (
            not is_critical and (
                token_ratio >= self.thresholds.soft_warning_ratio or
                turn_ratio >= self.thresholds.soft_warning_ratio
            )
        )

        status = "NORMAL"
        if is_critical:
            status = "CRITICAL_SATURATION"
        elif is_warning:
            status = "WARNING_SATURATION"

        return {
            "status": status,
            "turns_count": self.turns_count,
            "total_chars": self.total_chars,
            "estimated_tokens": est_tokens,
            "token_capacity_ratio": round(token_ratio, 3),
            "turn_capacity_ratio": round(turn_ratio, 3),
            "handover_recommended": is_critical or is_warning
        }

    def should_trigger_handover(self, dom_snapshot: Optional[DOMTelemetrySnapshot] = None) -> Tuple[bool, str]:
        """
        Determines if a handover is required based on DOM alerts or quantitative thresholds.
        """
        if dom_snapshot and (dom_snapshot.saturation_detected or dom_snapshot.input_disabled):
            reasons = ", ".join(dom_snapshot.saturation_reasons) if dom_snapshot.saturation_reasons else "DOM Input Blocked"
            return True, f"DOM Saturation Signal: {reasons}"

        est_tokens = int(self.total_chars / 3.7)
        if est_tokens >= self.thresholds.estimated_token_limit * self.thresholds.hard_saturation_ratio:
            return True, f"Estimated token threshold reached: {est_tokens}/{self.thresholds.estimated_token_limit}"

        if self.turns_count >= self.thresholds.max_turns:
            return True, f"Turn count limit reached: {self.turns_count}/{self.thresholds.max_turns}"

        return False, "Context within safe operational boundaries"


# ==============================================================================
# PART 4: HIGH SEMANTIC DENSITY PROMPT TEMPLATES (SPECTER-DSL)
# ==============================================================================

class SolPromptType(str, Enum):
    SYNTHESIS = "SYNTHESIS"
    REFACTOR = "REFACTOR"
    ADVERSARIAL_AUDIT = "ADVERSARIAL_AUDIT"
    CONTEXT_HANDOVER = "CONTEXT_HANDOVER"


@dataclass
class SolPromptTemplate:
    prompt_type: SolPromptType
    description: str
    template_str: str
    required_variables: List[str]

    def render(self, params: Dict[str, Any]) -> str:
        for v in self.required_variables:
            if v not in params:
                raise KeyError(f"Missing required parameter '{v}' for template {self.prompt_type.value}")
        rendered = self.template_str
        for k, v in params.items():
            rendered = rendered.replace(f"{{{k}}}", str(v))
        return rendered.strip()

    def estimate_tokens(self, params: Dict[str, Any]) -> int:
        rendered = self.render(params)
        return int(len(rendered) / 3.7)


class SolPromptEngine:
    """
    Engine providing high semantic density prompt templates formatted for Sol.
    Eliminates token waste, enforces deterministic contracts, and activates
    deep thinking without verbose chat preamble.
    """

    TEMPLATES: Dict[SolPromptType, SolPromptTemplate] = {
        SolPromptType.SYNTHESIS: SolPromptTemplate(
            prompt_type=SolPromptType.SYNTHESIS,
            description="High-density architecture and full module synthesis with strict typing and test harness.",
            template_str="""# SPECTER SOL SPEC // MODULE SYNTHESIS
:GOAL #{goal_id} @Sol act=synthesize.module.v1 target={target_module} timeout={timeout_s} key={idempotency_key}
:PLAN strat=standard_lib_deterministic error_model=fail_fast_typed
:VERIFY verifier=pytest_or_unittest assertion_min={min_assertions} coverage=strict

## 1. ARCHITECTURAL CONTRACT & INTENT
- Target Module: `{target_module}`
- Core Purpose: {intent_description}
- Invariants & Constraints:
{invariants_list}

## 2. INPUT & OUTPUT SPECIFICATION
- Input Parameters: {input_contract}
- Return Structures: {output_contract}
- Determinism Rule: RFC 8785 canonical JSON and SHA-256 for all state mutations.

## 3. IMPLEMENTATION REQUIREMENTS
1. 100% Python Standard Library (zero unauthorized external packages).
2. Complete, executable, production-grade code. ZERO mocks, ZERO placeholders, ZERO 'TODO' comments.
3. Include comprehensive unit test suite in separate block or self-contained runner.
4. Output cleanly formatted python code blocks.
""",
            required_variables=[
                "goal_id", "target_module", "timeout_s", "idempotency_key",
                "min_assertions", "intent_description", "invariants_list",
                "input_contract", "output_contract"
            ]
        ),

        SolPromptType.REFACTOR: SolPromptTemplate(
            prompt_type=SolPromptType.REFACTOR,
            description="Minimum correct diff refactor preserving public contracts and AST invariants.",
            template_str="""# SPECTER SOL SPEC // TARGETED REFACTOR (MINIMUM CORRECT DIFF)
:GOAL #{goal_id} @Sol act=refactor.symbol.v1 target={target_file} symbol={target_symbol}
:PLAN strat=minimum_correct_diff blast_radius=isolated preserve_contracts=true
:VERIFY verifier=regression_suite expected_status=PASS

## 1. REFACTOR DIRECTIVE
- Target File: `{target_file}`
- Target Symbol / Area: `{target_symbol}`
- Problem / Root Cause: {problem_description}
- Required Modification: {desired_behavior}

## 2. PRESERVATION CONSTRAINTS
- Existing public APIs, signatures, and database schemas MUST remain strictly backward-compatible.
- Minimal Diff Rule: Do NOT rewrite unrelated helper functions, imports, or formatting.
- Do NOT alter functional behavior outside `{target_symbol}`.

## 3. REQUIRED OUTPUT
- Output exact replacement code or targeted symbol definition ready for atomic injection.
- Include verification unit test proving bug resolution without regression.
""",
            required_variables=[
                "goal_id", "target_file", "target_symbol", "problem_description", "desired_behavior"
            ]
        ),

        SolPromptType.ADVERSARIAL_AUDIT: SolPromptTemplate(
            prompt_type=SolPromptType.ADVERSARIAL_AUDIT,
            description="Adversarial falsification protocol discovering race conditions, boundary bugs, and leaks.",
            template_str="""# SPECTER SOL SPEC // ADVERSARIAL AUDIT & FALSIFICATION
:GOAL #{goal_id} @Sol act=audit.falsify.v1 target={target_file}
:PLAN strat=skeptical_falsification focus=[concurrency,boundaries,overflow,stale_state]
:VERIFY verifier=adversarial_exploit_test verdict_expected=REVEAL_FLAWS

## 1. CODE UNDER SCRUTINY
Target File: `{target_file}`
Target Code / Logic:
```python
{code_content}
```

## 2. AUDIT AXES
Analyze and explicitly seek:
1. Concurrency anomalies (race conditions, unhandled locks, sqlite WAL concurrency conflicts).
2. State desynchronization (stale lease epochs, unverified hash chains, unhandled exceptions).
3. Boundary & Resource failures (file handle leaks, unbounded memory buffers, timeout overflows).

## 3. REQUIRED DELIVERABLE
1. Formal Vulnerability Ledger (Root Cause, Probability, Blast Radius).
2. Executable Pytest/Unittest suite specifically designed to fail against the current flawed code.
3. Hardened, corrected patch fixing the discovered defects.
""",
            required_variables=["goal_id", "target_file", "code_content"]
        ),

        SolPromptType.CONTEXT_HANDOVER: SolPromptTemplate(
            prompt_type=SolPromptType.CONTEXT_HANDOVER,
            description="Genesis prompt rehydrating full S_t state vector in a fresh ChatGPT thread.",
            template_str="""# SPECTER SOL SPEC // THREAD TRANSITION HANDOVER (GENESIS CONTEXT)
:GOAL #{goal_id} @Sol act=thread.handover.v1 prev_aggregate={prev_thread_id} seq={sequence}
:VERIFY state_root={state_root_hash} evidence={evidence_hash}

## 1. RECONSTITUTED SYSTEM STATE S_{sequence}
- **Active Goal (G)**: {goal_json}
- **Artifacts Manifest (A)**:
{artifacts_list}
- **Verified Test Proofs**:
{approved_tests_list}

## 2. OPERATIONAL ENVIRONMENT
- Host: Windows 10 x64, Python 3.12, Local Browser Named Pipe IPC.
- Directive: Maintain strict adherence to Specter Core Invariants (Zero token leaks, high density).

## 3. IMMEDIATE TASK
{immediate_task_instruction}
""",
            required_variables=[
                "goal_id", "prev_thread_id", "sequence", "state_root_hash",
                "evidence_hash", "goal_json", "artifacts_list",
                "approved_tests_list", "immediate_task_instruction"
            ]
        )
    }

    @classmethod
    def render(cls, prompt_type: SolPromptType, params: Dict[str, Any]) -> str:
        tpl = cls.TEMPLATES.get(prompt_type)
        if not tpl:
            raise ValueError(f"Unknown prompt template type: {prompt_type}")
        return tpl.render(params)

    @classmethod
    def estimate_tokens(cls, prompt_type: SolPromptType, params: Dict[str, Any]) -> int:
        tpl = cls.TEMPLATES.get(prompt_type)
        if not tpl:
            raise ValueError(f"Unknown prompt template type: {prompt_type}")
        return tpl.estimate_tokens(params)


# ==============================================================================
# PART 5: UNIFIED SOL TELEMETRY OBSERVER & ENGINE
# ==============================================================================

class SolTelemetryEngine:
    """
    Unified Orchestrator:
    - Ingests DOM Telemetry via Named Pipe or synthetic input.
    - Manages Thinking -> Coding state transition telemetry.
    - Monitors Context Saturation and generates S_t Checkpoint payloads.
    - Renders high semantic density prompts.
    """

    def __init__(
        self,
        tab_id: Optional[int] = None,
        client: Optional[Any] = None,
        on_transition: Optional[Callable[[TransitionRecord], None]] = None
    ):
        self.tab_id = tab_id
        self.client = client
        self.detector = SolStateTransitionDetector(on_transition=on_transition)
        self.saturation_sentinel = ContextSaturationSentinel()
        self.prompt_engine = SolPromptEngine()

    def connect_browser(self, tab_substring: str = "chatgpt.com") -> bool:
        """Connects to the local browser via Named Pipe and resolves ChatGPT tab."""
        if not CodexBrowserClient:
            return False
        try:
            if not self.client:
                self.client = CodexBrowserClient()
                self.client.connect()
            tabs = self.client.list_tabs()
            for t in tabs:
                if tab_substring in t.get("url", ""):
                    self.tab_id = t["id"]
                    return True
            if tabs:
                self.tab_id = tabs[0]["id"]
                return True
            return False
        except Exception as e:
            print(f"[!] Error connecting browser client: {e}", file=sys.stderr)
            return False

    def poll_dom(self) -> Optional[DOMTelemetrySnapshot]:
        """Evaluates DOM_TELEMETRY_JS safely in the attached tab and returns snapshot."""
        if not self.client or not self.tab_id:
            return None
        try:
            raw = self.client.evaluate(self.tab_id, DOM_TELEMETRY_JS)
            if raw and isinstance(raw, dict) and raw.get("success"):
                snapshot = DOMTelemetrySnapshot.from_dict(raw)
                self.detector.ingest_snapshot(snapshot)
                return snapshot
            return None
        except Exception as e:
            print(f"[!] DOM evaluation failed: {e}", file=sys.stderr)
            return None

    def create_checkpoint(
        self,
        aggregate_id: str,
        sequence: int,
        goals: Dict[str, Any],
        plan: List[Dict[str, Any]],
        tasks: Dict[str, Any],
        messages: List[Dict[str, Any]],
        artifacts_dir: Optional[Path] = None,
        approved_tests: Optional[List[PassedTestEvidence]] = None,
        prev_hash: str = "0" * 64
    ) -> TransitionCheckpoint:
        """
        Creates an immutable S_t = (G, P, T, M, A, X, C) Checkpoint
        with SHA-256 evidence hashing and test proofs.
        """
        artifacts_manifest: Dict[str, str] = {}
        if artifacts_dir and Path(artifacts_dir).is_dir():
            for p in Path(artifacts_dir).rglob("*"):
                if p.is_file() and not p.name.endswith(".pyc") and "__pycache__" not in p.parts:
                    rel_p = str(p.relative_to(artifacts_dir)).replace("\\", "/")
                    try:
                        artifacts_manifest[rel_p] = compute_file_sha256(p)
                    except Exception:
                        pass

        chk = TransitionCheckpoint(
            checkpoint_id=f"chk_{uuid.uuid4().hex[:12]}",
            aggregate_id=aggregate_id,
            sequence=sequence,
            created_at=time.time(),
            G=goals,
            P=plan,
            T=tasks,
            M=messages,
            A=artifacts_manifest,
            X={},
            C={"model": "sol_gpt_5_6_thinking", "transport": "named_pipe_v2"},
            approved_tests=approved_tests or [],
            previous_checkpoint_hash=prev_hash
        )
        chk.finalize()
        return chk


# ==============================================================================
# CLI / DIAGNOSTIC SMOKE TEST ENTRYPOINT
# ==============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("SPECTER SOL TELEMETRY & OBSERVABILITY ENGINE (v2.0)")
    print("=" * 70)

    # 1. Test Prompt Rendering
    engine = SolPromptEngine()
    rendered = engine.render(SolPromptType.SYNTHESIS, {
        "goal_id": "goal-001",
        "target_module": "specter_core.security",
        "timeout_s": 120,
        "idempotency_key": "sec-init-99",
        "min_assertions": 5,
        "intent_description": "Implement constant-time cryptographic token verification",
        "invariants_list": "- Must use hmac.compare_digest\n- Zero side-channel leaks\n- Deterministic error returns",
        "input_contract": "token_a: bytes, token_b: bytes",
        "output_contract": "bool"
    })
    tokens_est = engine.estimate_tokens(SolPromptType.SYNTHESIS, {
        "goal_id": "goal-001",
        "target_module": "specter_core.security",
        "timeout_s": 120,
        "idempotency_key": "sec-init-99",
        "min_assertions": 5,
        "intent_description": "Implement constant-time cryptographic token verification",
        "invariants_list": "- Must use hmac.compare_digest\n- Zero side-channel leaks\n- Deterministic error returns",
        "input_contract": "token_a: bytes, token_b: bytes",
        "output_contract": "bool"
    })

    print(f"[+] Rendered Prompt Preview ({tokens_est} est. tokens):")
    print("-" * 50)
    print(rendered[:350] + "\n[...]\n")
    print("-" * 50)

    # 2. Test Checkpoint Generation
    t_engine = SolTelemetryEngine()
    test_ev = PassedTestEvidence(
        test_id="test-01",
        suite_name="test_bridge_job_state.py",
        duration_s=0.24,
        passed_assertions=5,
        failed_assertions=0,
        stdout_sha256=sha256_digest(b"Ran 5 tests in 0.246s OK")
    )
    checkpoint = t_engine.create_checkpoint(
        aggregate_id="sol_thread_alpha",
        sequence=1,
        goals={"objective": "Validate canonical validator"},
        plan=[{"step": 1, "desc": "Write test"}, {"step": 2, "desc": "Verify"}],
        tasks={"active": "task-01"},
        messages=[{"role": "user", "text": "synthesize module"}],
        approved_tests=[test_ev]
    )
    print(f"[+] Checkpoint Created: {checkpoint.checkpoint_id}")
    print(f"    State Root Hash: {checkpoint.state_root_hash}")
    print(f"    Evidence Hash:   {checkpoint.evidence_hash}")

    genesis = checkpoint.generate_genesis_prompt("Synthesize next module: sol_state_recovery.py")
    print(f"\n[+] Generated Genesis Handover Prompt:\n{'-'*50}\n{genesis[:350]}\n[...]\n{'-'*50}")
    print("\n[✓] Diagnostic Smoke Test Completed Successfully.")
