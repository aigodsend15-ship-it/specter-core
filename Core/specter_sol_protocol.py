from __future__ import annotations

"""specter_sol_protocol.py

Zero-third-party, transport-neutral contract for an authorized SPECTER bridge.
No private ChatGPT/OpenAI endpoint is assumed or fabricated.

Public surface:
    send
    poll_thinking
    detect_saturation
    extract_code_blocks
    handoff_checkpoint

"poll_thinking" means observable bridge progress only: cursor/status/tool/artifact
and error events. It does not expose hidden chain-of-thought.

THINKING ACCOUNT / THREAD HANDOVER
1. S_t=(G,P,T,M,A,X,C) is the only resumable state:
   G = goals
   P = plan
   T = task/progress state
   M = durable memory
   A = artifact manifest
   X = execution/observation state
   C = constraints/invariants

2. Preserve exact invariants extractively: approved hashes, artifact IDs,
   model/session slugs, thread anchors, tests, generations, and protected
   constraints.

3. After significant commits, create a content-addressed checkpoint chained by
   parent_checkpoint + generation. Resume from the latest verified checkpoint,
   never from an ad-hoc transcript summary.

4. SOFT saturation => checkpoint and compact low-value narration.
   HARD saturation => stop new work, seal checkpoint, move to successor thread,
   rehydrate S_t, verify state_hash, and resume from the first incomplete task.

5. Preserve decisions, evidence, artifact hashes, verification results, and next
   actions rather than hidden reasoning. The successor ACKs checkpoint_hash before
   predecessor retirement. Only one lineage generation is writable at a time.

ANTI-FAILURE / N-SESSION RULES
1. Stable logical_operation_id -> stable operation_key. Persist it before
   submission; repeats return the existing durable state/result.

2. Failure definitely before submission:
       retry the same operation_key.
   Failure after submission may have occurred:
       mark AMBIGUOUS and reconcile observable state before any resend.
   Never create a new operation key merely to force a retry.

3. HTTP 429:
       honor Retry-After at quota_domain scope.
       Quota/billing exhaustion disables that quota domain until explicit reset.
       Do not rotate sibling sessions sharing the same quota domain to evade limits.

4. Proxy/network flakes:
       exponential backoff + deterministic hash-derived jitter.
       Retry only read-only polls or sends protected by the durable ledger.

5. Stream failover:
       permitted only before the first visible output delta.
       After a visible delta, pin the operation to the original session and surface
       interruption rather than merging two independent continuations.

6. N-session consistency:
       UNIQUE operation_key
       per-session fenced lease
       quota-domain cooldown
       CAS checkpoint generation
   Parallel workers may speculate/propose, but promotion is serialized.

Run:
    python specter_sol_protocol.py
"""

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any, Literal, Mapping, TypeAlias, TypedDict, cast


PROTOCOL_VERSION = "specter-sol/1"
JSONRPC_VERSION = "2.0"
MAX_SAFE_INTEGER = 9_007_199_254_740_991


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = (
    JSONScalar
    | list["JSONValue"]
    | dict[str, "JSONValue"]
)
JSONObject: TypeAlias = dict[str, JSONValue]


class ProtocolError(ValueError):
    """Protocol, canonicalization, or state-invariant violation."""


class DuplicateKeyError(ProtocolError):
    """JSON object contained a duplicate property name."""


class StateVector(TypedDict):
    G: JSONValue
    P: JSONValue
    T: JSONValue
    M: JSONValue
    A: JSONValue
    X: JSONValue
    C: JSONValue


class RPCRequest(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str
    method: str
    params: JSONObject


class CodeBlock(TypedDict):
    index: int
    language: str
    info: str
    code: str
    start: int
    end: int
    sha256: str


class Checkpoint(TypedDict):
    protocol: str
    thread_id: str
    generation: int
    created_unix_ms: int
    parent_checkpoint: str | None
    state: StateVector
    state_hash: str
    checkpoint_hash: str


class SaturationDecision(TypedDict):
    saturated: bool
    level: Literal["NONE", "SOFT", "HARD"]
    action: Literal["CONTINUE", "CHECKPOINT", "HANDOFF"]
    utilization_bps: int | None
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class SaturationTelemetry:
    """Explicit bridge-observable saturation signals."""

    context_used_tokens: int | None = None
    context_limit_tokens: int | None = None
    pending_output_reserve_tokens: int = 4096
    messages_since_checkpoint: int = 0
    stalled_polls: int = 0
    bridge_errors: int = 0

    def validate(self) -> None:
        for name in ("context_used_tokens", "context_limit_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ProtocolError(f"{name} must be >= 0")

        for name in (
            "pending_output_reserve_tokens",
            "messages_since_checkpoint",
            "stalled_polls",
            "bridge_errors",
        ):
            if getattr(self, name) < 0:
                raise ProtocolError(f"{name} must be >= 0")

        if self.context_limit_tokens == 0:
            raise ProtocolError("context_limit_tokens must be > 0")


@dataclass(frozen=True, slots=True)
class SaturationPolicy:
    soft_utilization_bps: int = 8200
    hard_utilization_bps: int = 9200
    checkpoint_every_messages: int = 24
    hard_stalled_polls: int = 4
    hard_bridge_errors: int = 3

    def validate(self) -> None:
        if not (
            0
            < self.soft_utilization_bps
            < self.hard_utilization_bps
            <= 10000
        ):
            raise ProtocolError("invalid utilization thresholds")

        if min(
            self.checkpoint_every_messages,
            self.hard_stalled_polls,
            self.hard_bridge_errors,
        ) <= 0:
            raise ProtocolError("policy counters must be > 0")


@dataclass(frozen=True, slots=True)
class RetryDirective:
    action: Literal[
        "RETRY",
        "COOLDOWN",
        "DISABLE_DOMAIN",
        "RECONCILE",
        "FAIL",
    ]
    delay_ms: int
    scope: Literal["operation", "session", "quota_domain"]
    reason: str


# ---------------------------------------------------------------------------
# RFC 8785 / JCS
# ---------------------------------------------------------------------------

_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


def _jcs_string(value: str) -> str:
    out = ['"']

    for char in value:
        codepoint = ord(char)

        if 0xD800 <= codepoint <= 0xDFFF:
            raise ProtocolError(
                "lone UTF-16 surrogate is invalid I-JSON"
            )

        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif codepoint <= 0x1F:
            out.append(f"\\u{codepoint:04x}")
        else:
            out.append(char)

    out.append('"')
    return "".join(out)


def _utf16_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-16-be", "strict")
    except UnicodeEncodeError as exc:
        raise ProtocolError("invalid Unicode object key") from exc


def _ecmascript_number(value: float) -> str:
    """Serialize one finite binary64 value in ECMAScript/JCS notation."""

    if not math.isfinite(value):
        raise ProtocolError(
            "NaN and Infinity are invalid JCS numbers"
        )

    if value == 0.0:
        return "0"

    sign = "-" if value < 0 else ""

    decimal_value = Decimal(repr(abs(value)))
    tuple_value = decimal_value.as_tuple()

    digits = "".join(map(str, tuple_value.digits)) or "0"
    exponent = int(tuple_value.exponent)

    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
        exponent += 1

    decimal_position = len(digits) + exponent
    magnitude = abs(value)

    if 1e-6 <= magnitude < 1e21:
        if decimal_position <= 0:
            body = (
                "0."
                + ("0" * (-decimal_position))
                + digits
            )
        elif decimal_position >= len(digits):
            body = (
                digits
                + ("0" * (decimal_position - len(digits)))
            )
        else:
            body = (
                digits[:decimal_position]
                + "."
                + digits[decimal_position:]
            )
    else:
        body = digits[0]

        if len(digits) > 1:
            body += "." + digits[1:]

        scientific_exponent = decimal_position - 1

        body += (
            "e"
            + ("+" if scientific_exponent >= 0 else "")
            + str(scientific_exponent)
        )

    return sign + body


def _binary64(value: int | float) -> float:
    try:
        number = float(value)
    except OverflowError as exc:
        raise ProtocolError(
            "number is outside IEEE-754 binary64 range"
        ) from exc

    if not math.isfinite(number):
        raise ProtocolError(
            "number is outside finite IEEE-754 binary64 range"
        )

    return number


def _jcs(value: JSONValue) -> str:
    if value is None:
        return "null"

    if value is True:
        return "true"

    if value is False:
        return "false"

    if isinstance(value, str):
        return _jcs_string(value)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _ecmascript_number(_binary64(value))

    if isinstance(value, list):
        return (
            "["
            + ",".join(_jcs(item) for item in value)
            + "]"
        )

    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ProtocolError(
                "JSON object keys must be strings"
            )

        items = sorted(
            value.items(),
            key=lambda item: _utf16_sort_key(item[0]),
        )

        return (
            "{"
            + ",".join(
                _jcs_string(key) + ":" + _jcs(item)
                for key, item in items
            )
            + "}"
        )

    raise ProtocolError(
        f"unsupported JSON type: {type(value).__name__}"
    )


def jcs_dumps(value: JSONValue) -> str:
    """Serialize a JSON value deterministically."""

    serialized = _jcs(value)

    try:
        serialized.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ProtocolError("invalid Unicode data") from exc

    return serialized


def jcs_bytes(value: JSONValue) -> bytes:
    return jcs_dumps(value).encode("utf-8")


def sha256_jcs(value: JSONValue) -> str:
    return sha256(jcs_bytes(value)).hexdigest()


def _object_pairs_no_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(
                f"duplicate JSON key: {key!r}"
            )
        result[key] = value

    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise ProtocolError(
        f"invalid non-finite JSON number: {value}"
    )


def jcs_loads(text: str) -> JSONValue:
    """Parse strict JSON and verify that it can be canonicalized."""

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_pairs_no_duplicates,
            parse_constant=_reject_nonfinite_constant,
        )
    except json.JSONDecodeError as exc:
        raise ProtocolError(str(exc)) from exc

    result = cast(JSONValue, parsed)

    # Also validates Unicode and numeric domain.
    jcs_dumps(result)

    return result


# ---------------------------------------------------------------------------
# Protocol schema
# ---------------------------------------------------------------------------

def protocol_schema() -> JSONObject:
    """Return the machine-readable protocol manifest."""

    return {
        "protocol": PROTOCOL_VERSION,
        "wire": "json-rpc-2.0-ish",
        "transport": "external-authorized-bridge",
        "methods": {
            "send": {
                "location": "bridge",
                "effect": "side_effecting",
                "idempotency": "operation_key",
            },
            "poll_thinking": {
                "location": "bridge",
                "effect": "read_only",
                "visibility": "observable_progress_only",
            },
            "detect_saturation": {
                "location": "local",
                "effect": "pure",
            },
            "extract_code_blocks": {
                "location": "local",
                "effect": "pure",
            },
            "handoff_checkpoint": {
                "location": "local",
                "effect": "pure",
                "state_vector": [
                    "G",
                    "P",
                    "T",
                    "M",
                    "A",
                    "X",
                    "C",
                ],
            },
        },
    }


def _require_nonempty(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(
            f"{name} must be a non-empty string"
        )

    return value


def _text_hash(value: str) -> str:
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ProtocolError(
            "text contains invalid Unicode"
        ) from exc

    return sha256(encoded).hexdigest()


def _rpc_request(
    method: str,
    params: JSONObject,
    identity: JSONValue,
) -> RPCRequest:
    request_id = sha256_jcs(
        {
            "protocol": PROTOCOL_VERSION,
            "method": method,
            "identity": identity,
        }
    )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def send(
    *,
    session_id: str,
    text: str,
    client_id: str,
    logical_operation_id: str,
    sequence: int,
    parent_checkpoint: str | None = None,
) -> RPCRequest:
    """Build an idempotent, side-effecting bridge send request."""

    _require_nonempty("session_id", session_id)
    _require_nonempty("client_id", client_id)
    _require_nonempty(
        "logical_operation_id",
        logical_operation_id,
    )

    if sequence < 0:
        raise ProtocolError(
            "sequence must be >= 0"
        )

    text_sha256 = _text_hash(text)

    identity: JSONObject = {
        "client_id": client_id,
        "logical_operation_id": logical_operation_id,
        "session_id": session_id,
        "text_sha256": text_sha256,
        "parent_checkpoint": parent_checkpoint,
    }

    operation_key = sha256_jcs(
        {
            "namespace": "specter-sol/send",
            "identity": identity,
        }
    )

    params: JSONObject = {
        "protocol": PROTOCOL_VERSION,
        "session_id": session_id,
        "client_id": client_id,
        "logical_operation_id": logical_operation_id,
        "operation_key": operation_key,
        "sequence": sequence,
        "text": text,
        "text_sha256": text_sha256,
        "parent_checkpoint": parent_checkpoint,
        "stream": True,
    }

    # For a side-effecting request the JSON-RPC-ish id itself is the
    # idempotency identity.
    return {
        "jsonrpc": "2.0",
        "id": operation_key,
        "method": "send",
        "params": params,
    }


def poll_thinking(
    *,
    session_id: str,
    client_id: str,
    cursor: str | None,
    poll_sequence: int,
) -> RPCRequest:
    """Poll observable bridge progress only."""

    _require_nonempty("session_id", session_id)
    _require_nonempty("client_id", client_id)

    if poll_sequence < 0:
        raise ProtocolError(
            "poll_sequence must be >= 0"
        )

    params: JSONObject = {
        "protocol": PROTOCOL_VERSION,
        "session_id": session_id,
        "client_id": client_id,
        "cursor": cursor,
        "poll_sequence": poll_sequence,
        "visibility": "observable_progress_only",
    }

    identity: JSONObject = {
        "session_id": session_id,
        "client_id": client_id,
        "cursor": cursor,
        "poll_sequence": poll_sequence,
    }

    return _rpc_request(
        "poll_thinking",
        params,
        identity,
    )


# ---------------------------------------------------------------------------
# Saturation / checkpointing
# ---------------------------------------------------------------------------

def detect_saturation(
    telemetry: SaturationTelemetry,
    policy: SaturationPolicy = SaturationPolicy(),
) -> SaturationDecision:
    """Classify session saturation from explicit telemetry."""

    telemetry.validate()
    policy.validate()

    reasons: list[str] = []
    utilization_bps: int | None = None

    level: Literal[
        "NONE",
        "SOFT",
        "HARD",
    ] = "NONE"

    if (
        telemetry.context_used_tokens is not None
        and telemetry.context_limit_tokens
    ):
        reserved_usage = (
            telemetry.context_used_tokens
            + telemetry.pending_output_reserve_tokens
        )

        utilization_bps = min(
            10000,
            (
                reserved_usage
                * 10000
                // telemetry.context_limit_tokens
            ),
        )

        if (
            utilization_bps
            >= policy.hard_utilization_bps
        ):
            level = "HARD"
            reasons.append(
                "context_reserve_crossed_hard_threshold"
            )
        elif (
            utilization_bps
            >= policy.soft_utilization_bps
        ):
            level = "SOFT"
            reasons.append(
                "context_reserve_crossed_soft_threshold"
            )

    if (
        telemetry.stalled_polls
        >= policy.hard_stalled_polls
    ):
        level = "HARD"
        reasons.append(
            "observable_progress_stalled"
        )

    if (
        telemetry.bridge_errors
        >= policy.hard_bridge_errors
    ):
        level = "HARD"
        reasons.append(
            "bridge_error_budget_exhausted"
        )

    if (
        level == "NONE"
        and telemetry.messages_since_checkpoint
        >= policy.checkpoint_every_messages
    ):
        level = "SOFT"
        reasons.append(
            "checkpoint_interval_reached"
        )

    action: Literal[
        "CONTINUE",
        "CHECKPOINT",
        "HANDOFF",
    ]

    if level == "HARD":
        action = "HANDOFF"
    elif level == "SOFT":
        action = "CHECKPOINT"
    else:
        action = "CONTINUE"

    return {
        "saturated": level != "NONE",
        "level": level,
        "action": action,
        "utilization_bps": utilization_bps,
        "reasons": reasons,
    }


def _validate_exact_state_value(
    value: JSONValue,
    path: str = "$",
) -> None:
    """Guarantee semantic round-trip for S_t."""

    if (
        value is None
        or isinstance(value, bool)
        or isinstance(value, str)
    ):
        return

    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ProtocolError(
                f"{path}: integer exceeds exact I-JSON "
                "range; encode it as a string"
            )
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError(
                f"{path}: non-finite float"
            )
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_exact_state_value(
                item,
                f"{path}[{index}]",
            )
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError(
                    f"{path}: object key is not a string"
                )

            _validate_exact_state_value(
                item,
                f"{path}.{key}",
            )
        return

    raise ProtocolError(
        f"{path}: unsupported JSON type "
        f"{type(value).__name__}"
    )


def _validate_state_vector(
    state: Mapping[str, JSONValue],
) -> StateVector:
    required = (
        "G",
        "P",
        "T",
        "M",
        "A",
        "X",
        "C",
    )

    if set(state) != set(required):
        missing = sorted(
            set(required) - set(state)
        )
        extra = sorted(
            set(state) - set(required)
        )

        raise ProtocolError(
            "invalid state vector; "
            f"missing={missing}, extra={extra}"
        )

    normalized = cast(
        StateVector,
        {
            key: state[key]
            for key in required
        },
    )

    _validate_exact_state_value(
        cast(JSONValue, normalized)
    )

    jcs_dumps(
        cast(JSONValue, normalized)
    )

    return normalized


def handoff_checkpoint(
    *,
    thread_id: str,
    generation: int,
    created_unix_ms: int,
    state: Mapping[str, JSONValue],
    parent_checkpoint: str | None,
) -> Checkpoint:
    """Create a deterministic chained checkpoint for S_t."""

    _require_nonempty("thread_id", thread_id)

    if generation < 0:
        raise ProtocolError(
            "generation must be >= 0"
        )

    if created_unix_ms < 0:
        raise ProtocolError(
            "created_unix_ms must be >= 0"
        )

    if (
        generation == 0
        and parent_checkpoint is not None
    ):
        raise ProtocolError(
            "generation 0 cannot have a parent"
        )

    if (
        generation > 0
        and not parent_checkpoint
    ):
        raise ProtocolError(
            "generation > 0 requires a parent"
        )

    normalized_state = _validate_state_vector(
        state
    )

    state_hash = sha256_jcs(
        cast(JSONValue, normalized_state)
    )

    base: JSONObject = {
        "protocol": PROTOCOL_VERSION,
        "thread_id": thread_id,
        "generation": generation,
        "created_unix_ms": created_unix_ms,
        "parent_checkpoint": parent_checkpoint,
        "state": cast(
            JSONValue,
            normalized_state,
        ),
        "state_hash": state_hash,
    }

    checkpoint_hash = sha256_jcs(base)

    return {
        "protocol": PROTOCOL_VERSION,
        "thread_id": thread_id,
        "generation": generation,
        "created_unix_ms": created_unix_ms,
        "parent_checkpoint": parent_checkpoint,
        "state": normalized_state,
        "state_hash": state_hash,
        "checkpoint_hash": checkpoint_hash,
    }


def verify_checkpoint(
    checkpoint: Mapping[str, Any],
) -> bool:
    """Verify checkpoint structure and content hashes."""

    try:
        if (
            checkpoint.get("protocol")
            != PROTOCOL_VERSION
        ):
            return False

        state = _validate_state_vector(
            cast(
                Mapping[str, JSONValue],
                checkpoint["state"],
            )
        )

        if (
            sha256_jcs(
                cast(JSONValue, state)
            )
            != checkpoint["state_hash"]
        ):
            return False

        base: JSONObject = {
            "protocol": cast(
                str,
                checkpoint["protocol"],
            ),
            "thread_id": cast(
                str,
                checkpoint["thread_id"],
            ),
            "generation": cast(
                int,
                checkpoint["generation"],
            ),
            "created_unix_ms": cast(
                int,
                checkpoint["created_unix_ms"],
            ),
            "parent_checkpoint": cast(
                str | None,
                checkpoint["parent_checkpoint"],
            ),
            "state": cast(
                JSONValue,
                state,
            ),
            "state_hash": cast(
                str,
                checkpoint["state_hash"],
            ),
        }

        return (
            sha256_jcs(base)
            == checkpoint["checkpoint_hash"]
        )

    except (
        KeyError,
        TypeError,
        ProtocolError,
    ):
        return False


# ---------------------------------------------------------------------------
# Markdown fenced-code extraction
# ---------------------------------------------------------------------------

_FENCE_OPEN = re.compile(
    r"(?m)^"
    r"(?P<indent> {0,3})"
    r"(?P<fence>`{3,}|~{3,})"
    r"(?P<info>[^\n\r]*)"
    r"\r?$"
)


def extract_code_blocks(
    text: str,
) -> list[CodeBlock]:
    """Extract deterministic fenced Markdown code blocks."""

    blocks: list[CodeBlock] = []
    position = 0

    while True:
        opening = _FENCE_OPEN.search(
            text,
            position,
        )

        if opening is None:
            break

        fence = opening.group("fence")
        fence_character = fence[0]
        minimum_length = len(fence)

        info = (
            opening.group("info")
            .strip()
        )

        code_start = opening.end()

        if (
            code_start < len(text)
            and text[code_start] == "\r"
        ):
            code_start += 1

        if (
            code_start < len(text)
            and text[code_start] == "\n"
        ):
            code_start += 1

        closing_expression = re.compile(
            rf"(?m)^ {{0,3}}"
            rf"{re.escape(fence_character)}"
            rf"{{{minimum_length},}}"
            rf"[ \t]*\r?$"
        )

        closing = closing_expression.search(
            text,
            code_start,
        )

        if closing is None:
            position = opening.end()
            continue

        code = text[
            code_start:closing.start()
        ]

        if code.endswith("\r\n"):
            code = code[:-2]
        elif code.endswith("\n"):
            code = code[:-1]

        language = (
            info.split(None, 1)[0].lower()
            if info
            else ""
        )

        blocks.append(
            {
                "index": len(blocks),
                "language": language,
                "info": info,
                "code": code,
                "start": opening.start(),
                "end": closing.end(),
                "sha256": _text_hash(code),
            }
        )

        position = closing.end()

    return blocks


# ---------------------------------------------------------------------------
# Failure policy
# ---------------------------------------------------------------------------

def retry_delay_ms(
    *,
    attempt: int,
    operation_key: str,
    base_ms: int = 500,
    cap_ms: int = 30_000,
) -> int:
    """Deterministic full-jitter exponential backoff."""

    if attempt < 0:
        raise ProtocolError(
            "attempt must be >= 0"
        )

    if base_ms <= 0 or cap_ms <= 0:
        raise ProtocolError(
            "base_ms and cap_ms must be > 0"
        )

    ceiling = min(
        cap_ms,
        base_ms
        * (2 ** min(attempt, 20)),
    )

    digest = sha256(
        (
            f"{operation_key}:{attempt}"
        ).encode("utf-8")
    ).digest()

    sample = int.from_bytes(
        digest[:8],
        "big",
    )

    return sample % (ceiling + 1)


def failure_directive(
    *,
    http_status: int | None,
    retry_after_ms: int | None,
    attempt: int,
    operation_key: str,
    submission_may_have_happened: bool,
    quota_exhausted: bool = False,
) -> RetryDirective:
    """Convert a failure into a safe retry/reconciliation action."""

    if (
        retry_after_ms is not None
        and retry_after_ms < 0
    ):
        raise ProtocolError(
            "retry_after_ms must be >= 0"
        )

    if submission_may_have_happened:
        return RetryDirective(
            action="RECONCILE",
            delay_ms=0,
            scope="operation",
            reason=(
                "submission_outcome_is_ambiguous"
            ),
        )

    if http_status == 429 and quota_exhausted:
        return RetryDirective(
            action="DISABLE_DOMAIN",
            delay_ms=0,
            scope="quota_domain",
            reason=(
                "quota_or_billing_exhausted"
            ),
        )

    if http_status == 429:
        delay = (
            retry_after_ms
            if retry_after_ms is not None
            else retry_delay_ms(
                attempt=attempt,
                operation_key=operation_key,
                base_ms=1000,
                cap_ms=60_000,
            )
        )

        return RetryDirective(
            action="COOLDOWN",
            delay_ms=delay,
            scope="quota_domain",
            reason="rate_limited",
        )

    if (
        http_status is None
        or http_status
        in {
            408,
            425,
            502,
            503,
            504,
        }
    ):
        return RetryDirective(
            action="RETRY",
            delay_ms=retry_delay_ms(
                attempt=attempt,
                operation_key=operation_key,
            ),
            scope="operation",
            reason=(
                "transient_transport_or_proxy_failure"
            ),
        )

    return RetryDirective(
        action="FAIL",
        delay_ms=0,
        scope="operation",
        reason=(
            f"non_retryable_http_{http_status}"
        ),
    )


# ---------------------------------------------------------------------------
# Durable coordination
# ---------------------------------------------------------------------------

class SQLiteCoordinationLedger:
    """WAL ledger for farm-wide consistency.

    Guarantees:
      - unique logical operation identity
      - durable operation state
      - fenced single-writer session leases
      - quota-domain cooldown/disable state
      - compare-and-swap checkpoint promotion
    """

    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(
        self,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
        )

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA journal_mode=WAL"
        )
        connection.execute(
            "PRAGMA synchronous=FULL"
        )

        return connection

    def _initialize(
        self,
    ) -> None:
        connection = self._connect()

        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    operation_key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    state TEXT NOT NULL
                        CHECK (
                            state IN (
                                'PENDING',
                                'ACKED',
                                'AMBIGUOUS',
                                'FAILED'
                            )
                        ),
                    owner TEXT NOT NULL,
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL,
                    response_hash TEXT
                );

                CREATE TABLE IF NOT EXISTS session_leases (
                    session_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    lease_until_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quota_domains (
                    quota_domain TEXT PRIMARY KEY,
                    cooldown_until_ms INTEGER NOT NULL,
                    disabled INTEGER NOT NULL
                        CHECK (disabled IN (0, 1)),
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    checkpoint_hash TEXT NOT NULL UNIQUE,
                    parent_checkpoint TEXT,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (
                        thread_id,
                        generation
                    )
                );
                """
            )
        finally:
            connection.close()

    def begin_operation(
        self,
        *,
        operation_key: str,
        request_hash: str,
        owner: str,
        now_ms: int,
    ) -> Literal[
        "NEW",
        "PENDING",
        "ACKED",
        "AMBIGUOUS",
        "FAILED",
    ]:
        """Atomically claim or inspect an operation."""

        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT request_hash, state
                FROM operations
                WHERE operation_key=?
                """,
                (operation_key,),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO operations
                    VALUES (
                        ?, ?, 'PENDING',
                        ?, ?, ?, NULL
                    )
                    """,
                    (
                        operation_key,
                        request_hash,
                        owner,
                        now_ms,
                        now_ms,
                    ),
                )

                connection.commit()
                return "NEW"

            if (
                row["request_hash"]
                != request_hash
            ):
                raise ProtocolError(
                    "operation_key collision "
                    "with different request_hash"
                )

            state = cast(
                Literal[
                    "PENDING",
                    "ACKED",
                    "AMBIGUOUS",
                    "FAILED",
                ],
                row["state"],
            )

            connection.commit()
            return state

        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def finish_operation(
        self,
        *,
        operation_key: str,
        state: Literal[
            "ACKED",
            "AMBIGUOUS",
            "FAILED",
        ],
        now_ms: int,
        response_hash: str | None = None,
    ) -> None:
        """Persist a terminal/ambiguous operation state."""

        connection = self._connect()

        try:
            cursor = connection.execute(
                """
                UPDATE operations
                SET
                    state=?,
                    updated_ms=?,
                    response_hash=?
                WHERE operation_key=?
                """,
                (
                    state,
                    now_ms,
                    response_hash,
                    operation_key,
                ),
            )

            if cursor.rowcount != 1:
                raise ProtocolError(
                    "unknown operation_key"
                )
        finally:
            connection.close()

    def acquire_session_lease(
        self,
        *,
        session_id: str,
        owner: str,
        now_ms: int,
        lease_ms: int,
    ) -> int | None:
        """Acquire/renew a fenced single-writer lease.

        Returns the fencing epoch or None when another worker owns
        an unexpired lease.
        """

        if lease_ms <= 0:
            raise ProtocolError(
                "lease_ms must be > 0"
            )

        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT
                    owner,
                    epoch,
                    lease_until_ms
                FROM session_leases
                WHERE session_id=?
                """,
                (session_id,),
            ).fetchone()

            lease_until = (
                now_ms + lease_ms
            )

            if row is None:
                epoch = 1

                connection.execute(
                    """
                    INSERT INTO session_leases
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        owner,
                        epoch,
                        lease_until,
                    ),
                )

                connection.commit()
                return epoch

            if row["owner"] == owner:
                epoch = int(
                    row["epoch"]
                )

                connection.execute(
                    """
                    UPDATE session_leases
                    SET lease_until_ms=?
                    WHERE session_id=?
                    """,
                    (
                        lease_until,
                        session_id,
                    ),
                )

                connection.commit()
                return epoch

            if (
                int(row["lease_until_ms"])
                <= now_ms
            ):
                epoch = (
                    int(row["epoch"]) + 1
                )

                connection.execute(
                    """
                    UPDATE session_leases
                    SET
                        owner=?,
                        epoch=?,
                        lease_until_ms=?
                    WHERE session_id=?
                    """,
                    (
                        owner,
                        epoch,
                        lease_until,
                        session_id,
                    ),
                )

                connection.commit()
                return epoch

            connection.commit()
            return None

        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def set_quota_domain(
        self,
        *,
        quota_domain: str,
        cooldown_until_ms: int,
        disabled: bool,
        reason: str,
    ) -> None:
        """Increase cooldown and/or disable a quota domain."""

        connection = self._connect()

        try:
            connection.execute(
                """
                INSERT INTO quota_domains
                    VALUES (?, ?, ?, ?)
                ON CONFLICT(quota_domain)
                DO UPDATE SET
                    cooldown_until_ms=
                        MAX(
                            quota_domains.cooldown_until_ms,
                            excluded.cooldown_until_ms
                        ),
                    disabled=
                        MAX(
                            quota_domains.disabled,
                            excluded.disabled
                        ),
                    reason=excluded.reason
                """,
                (
                    quota_domain,
                    cooldown_until_ms,
                    int(disabled),
                    reason,
                ),
            )
        finally:
            connection.close()

    def reset_quota_domain(
        self,
        *,
        quota_domain: str,
    ) -> None:
        """Explicit operator reset for disabled/cooldown state."""

        connection = self._connect()

        try:
            connection.execute(
                """
                DELETE FROM quota_domains
                WHERE quota_domain=?
                """,
                (quota_domain,),
            )
        finally:
            connection.close()

    def quota_domain_available(
        self,
        *,
        quota_domain: str,
        now_ms: int,
    ) -> bool:
        """Return whether the quota domain may accept work."""

        connection = self._connect()

        try:
            row = connection.execute(
                """
                SELECT
                    cooldown_until_ms,
                    disabled
                FROM quota_domains
                WHERE quota_domain=?
                """,
                (quota_domain,),
            ).fetchone()

            if row is None:
                return True

            return (
                not bool(row["disabled"])
                and int(
                    row["cooldown_until_ms"]
                ) <= now_ms
            )
        finally:
            connection.close()

    def append_checkpoint_cas(
        self,
        *,
        checkpoint: Checkpoint,
        expected_generation: int,
    ) -> None:
        """CAS-promote one checkpoint to the lineage head."""

        if not verify_checkpoint(
            checkpoint
        ):
            raise ProtocolError(
                "checkpoint verification failed"
            )

        thread_id = checkpoint[
            "thread_id"
        ]

        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT
                    generation,
                    checkpoint_hash
                FROM checkpoints
                WHERE thread_id=?
                ORDER BY generation DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()

            current_generation = (
                -1
                if row is None
                else int(row["generation"])
            )

            current_hash = (
                None
                if row is None
                else cast(
                    str,
                    row["checkpoint_hash"],
                )
            )

            if (
                current_generation
                != expected_generation
            ):
                raise ProtocolError(
                    "checkpoint CAS failed: "
                    f"expected "
                    f"{expected_generation}, "
                    f"got {current_generation}"
                )

            if (
                checkpoint["generation"]
                != current_generation + 1
            ):
                raise ProtocolError(
                    "checkpoint generation "
                    "is not sequential"
                )

            if (
                checkpoint["parent_checkpoint"]
                != current_hash
            ):
                raise ProtocolError(
                    "checkpoint parent does "
                    "not match lineage head"
                )

            connection.execute(
                """
                INSERT INTO checkpoints
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    checkpoint["generation"],
                    checkpoint[
                        "checkpoint_hash"
                    ],
                    checkpoint[
                        "parent_checkpoint"
                    ],
                    jcs_bytes(
                        cast(
                            JSONValue,
                            checkpoint,
                        )
                    ),
                ),
            )

            connection.commit()

        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def load_head(
        self,
        thread_id: str,
    ) -> Checkpoint | None:
        """Load and verify the latest checkpoint."""

        connection = self._connect()

        try:
            row = connection.execute(
                """
                SELECT payload
                FROM checkpoints
                WHERE thread_id=?
                ORDER BY generation DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()

            if row is None:
                return None

            value = jcs_loads(
                bytes(
                    row["payload"]
                ).decode("utf-8")
            )

            if not isinstance(
                value,
                dict,
            ):
                raise ProtocolError(
                    "stored checkpoint is "
                    "not a JSON object"
                )

            checkpoint = cast(
                Checkpoint,
                value,
            )

            if not verify_checkpoint(
                checkpoint
            ):
                raise ProtocolError(
                    "stored checkpoint failed "
                    "verification"
                )

            return checkpoint

        finally:
            connection.close()


# ---------------------------------------------------------------------------
# Embedded standalone tests
# ---------------------------------------------------------------------------

class SpecterSolProtocolTests(
    unittest.TestCase
):
    def test_jcs_reference_shape(
        self,
    ) -> None:
        value: JSONValue = {
            "numbers": [
                333333333.33333329,
                1e30,
                4.50,
                2e-3,
                1e-27,
            ],
            "string": (
                "€$\u000f\nA'B\"\\\\\"/"
            ),
            "literals": [
                None,
                True,
                False,
            ],
        }

        self.assertEqual(
            jcs_dumps(value),
            (
                '{"literals":[null,true,false],'
                '"numbers":['
                '333333333.3333333,'
                '1e+30,'
                '4.5,'
                '0.002,'
                '1e-27],'
                '"string":'
                '"€$\\u000f\\nA\'B\\"'
                '\\\\\\\\\\"/"}'
            ),
        )

    def test_jcs_utf16_key_order(
        self,
    ) -> None:
        value: JSONValue = {
            "€": "Euro Sign",
            "\r": "Carriage Return",
            "דּ": (
                "Hebrew Letter Dalet "
                "With Dagesh"
            ),
            "1": "One",
            "😀": "Emoji",
            "\u0080": "Control",
            "ö": "Latin O Diaeresis",
        }

        canonical = jcs_dumps(
            value
        )

        parsed = cast(
            dict[str, JSONValue],
            jcs_loads(canonical),
        )

        self.assertEqual(
            list(parsed),
            [
                "\r",
                "1",
                "\u0080",
                "ö",
                "€",
                "😀",
                "דּ",
            ],
        )

    def test_jcs_number_vectors(
        self,
    ) -> None:
        vectors = {
            "0000000000000000":
                "0",
            "8000000000000000":
                "0",
            "0000000000000001":
                "5e-324",
            "8000000000000001":
                "-5e-324",
            "7fefffffffffffff":
                "1.7976931348623157e+308",
            "ffefffffffffffff":
                "-1.7976931348623157e+308",
            "4340000000000000":
                "9007199254740992",
            "c340000000000000":
                "-9007199254740992",
            "4430000000000000":
                "295147905179352830000",
            "44b52d02c7e14af5":
                "9.999999999999997e+22",
            "44b52d02c7e14af6":
                "1e+23",
            "44b52d02c7e14af7":
                "1.0000000000000001e+23",
            "444b1ae4d6e2ef4e":
                "999999999999999700000",
            "444b1ae4d6e2ef4f":
                "999999999999999900000",
            "444b1ae4d6e2ef50":
                "1e+21",
            "3eb0c6f7a0b5ed8c":
                "9.999999999999997e-7",
            "3eb0c6f7a0b5ed8d":
                "0.000001",
            "41b3de4355555553":
                "333333333.3333332",
            "41b3de4355555554":
                "333333333.33333325",
            "41b3de4355555555":
                "333333333.3333333",
            "41b3de4355555556":
                "333333333.3333334",
            "41b3de4355555557":
                "333333333.33333343",
            "becbf647612f3696":
                "-0.0000033333333333333333",
            "43143ff3c1cb0959":
                "1424953923781206.2",
        }

        for bits, expected in vectors.items():
            value = struct.unpack(
                ">d",
                bytes.fromhex(bits),
            )[0]

            self.assertEqual(
                jcs_dumps(value),
                expected,
                bits,
            )

        with self.assertRaises(
            ProtocolError
        ):
            jcs_dumps(float("nan"))

        with self.assertRaises(
            ProtocolError
        ):
            jcs_dumps(float("inf"))

    def test_send_idempotency(
        self,
    ) -> None:
        first = send(
            session_id="session-1",
            text="compute",
            client_id="worker-a",
            logical_operation_id=(
                "task-7/step-2"
            ),
            sequence=9,
        )

        second = send(
            session_id="session-1",
            text="compute",
            client_id="worker-a",
            logical_operation_id=(
                "task-7/step-2"
            ),
            sequence=9,
        )

        self.assertEqual(
            first,
            second,
        )

        self.assertEqual(
            first["id"],
            first["params"][
                "operation_key"
            ],
        )

    def test_observable_polling(
        self,
    ) -> None:
        request = poll_thinking(
            session_id="session-1",
            client_id="worker-a",
            cursor="cursor-9",
            poll_sequence=2,
        )

        self.assertEqual(
            request["params"][
                "visibility"
            ],
            "observable_progress_only",
        )

    def test_saturation(
        self,
    ) -> None:
        result = detect_saturation(
            SaturationTelemetry(
                context_used_tokens=90_000,
                context_limit_tokens=100_000,
                pending_output_reserve_tokens=4_000,
            )
        )

        self.assertEqual(
            result["action"],
            "HANDOFF",
        )

    def test_code_blocks(
        self,
    ) -> None:
        source = (
            "before\n"
            "```python\n"
            "print('x')\n"
            "```\n"
            "after\n"
            "~~~json\n"
            '{"a":1}\n'
            "~~~"
        )

        blocks = extract_code_blocks(
            source
        )

        self.assertEqual(
            len(blocks),
            2,
        )

        self.assertEqual(
            blocks[0]["language"],
            "python",
        )

        self.assertEqual(
            blocks[0]["code"],
            "print('x')",
        )

        self.assertEqual(
            blocks[1]["language"],
            "json",
        )

    def test_checkpoint_and_ledger(
        self,
    ) -> None:
        state: StateVector = {
            "G": {
                "goal": "ship",
            },
            "P": [
                "build",
                "verify",
            ],
            "T": {
                "done": 1,
            },
            "M": {
                "invariant": "exact",
            },
            "A": {
                "files": [
                    "a.py",
                ],
            },
            "X": {
                "cursor": "c1",
            },
            "C": {
                "no_private_api": True,
            },
        }

        invalid_state = dict(state)
        invalid_state["T"] = {
            "too_large":
                MAX_SAFE_INTEGER + 1,
        }

        with self.assertRaises(
            ProtocolError
        ):
            handoff_checkpoint(
                thread_id="bad-thread",
                generation=0,
                created_unix_ms=1,
                state=invalid_state,
                parent_checkpoint=None,
            )

        checkpoint = handoff_checkpoint(
            thread_id="thread-a",
            generation=0,
            created_unix_ms=(
                1_800_000_000_000
            ),
            state=state,
            parent_checkpoint=None,
        )

        self.assertTrue(
            verify_checkpoint(
                checkpoint
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            ledger = SQLiteCoordinationLedger(
                Path(directory) / "coord.db"
            )

            ledger.append_checkpoint_cas(
                checkpoint=checkpoint,
                expected_generation=-1,
            )

            head = ledger.load_head(
                "thread-a"
            )

            self.assertIsNotNone(
                head
            )

            assert head is not None

            self.assertEqual(
                head["checkpoint_hash"],
                checkpoint[
                    "checkpoint_hash"
                ],
            )

            request = send(
                session_id="session-1",
                text="hello",
                client_id="worker-1",
                logical_operation_id="op-1",
                sequence=1,
            )

            operation_key = cast(
                str,
                request["params"][
                    "operation_key"
                ],
            )

            request_hash = sha256_jcs(
                cast(
                    JSONValue,
                    request,
                )
            )

            self.assertEqual(
                ledger.begin_operation(
                    operation_key=(
                        operation_key
                    ),
                    request_hash=(
                        request_hash
                    ),
                    owner="worker-1",
                    now_ms=100,
                ),
                "NEW",
            )

            self.assertEqual(
                ledger.begin_operation(
                    operation_key=(
                        operation_key
                    ),
                    request_hash=(
                        request_hash
                    ),
                    owner="worker-2",
                    now_ms=101,
                ),
                "PENDING",
            )

            ledger.finish_operation(
                operation_key=operation_key,
                state="ACKED",
                now_ms=102,
                response_hash="abc",
            )

            self.assertEqual(
                ledger.begin_operation(
                    operation_key=(
                        operation_key
                    ),
                    request_hash=(
                        request_hash
                    ),
                    owner="worker-3",
                    now_ms=103,
                ),
                "ACKED",
            )

    def test_lease_and_quota_domain(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = SQLiteCoordinationLedger(
                Path(directory) / "coord.db"
            )

            self.assertEqual(
                ledger.acquire_session_lease(
                    session_id="session-1",
                    owner="worker-a",
                    now_ms=100,
                    lease_ms=50,
                ),
                1,
            )

            self.assertIsNone(
                ledger.acquire_session_lease(
                    session_id="session-1",
                    owner="worker-b",
                    now_ms=120,
                    lease_ms=50,
                )
            )

            self.assertEqual(
                ledger.acquire_session_lease(
                    session_id="session-1",
                    owner="worker-b",
                    now_ms=151,
                    lease_ms=50,
                ),
                2,
            )

            ledger.set_quota_domain(
                quota_domain="quota-a",
                cooldown_until_ms=500,
                disabled=False,
                reason="429",
            )

            self.assertFalse(
                ledger.quota_domain_available(
                    quota_domain="quota-a",
                    now_ms=499,
                )
            )

            self.assertTrue(
                ledger.quota_domain_available(
                    quota_domain="quota-a",
                    now_ms=500,
                )
            )

            ledger.set_quota_domain(
                quota_domain="quota-a",
                cooldown_until_ms=500,
                disabled=True,
                reason="quota",
            )

            self.assertFalse(
                ledger.quota_domain_available(
                    quota_domain="quota-a",
                    now_ms=9999,
                )
            )

            ledger.reset_quota_domain(
                quota_domain="quota-a"
            )

            self.assertTrue(
                ledger.quota_domain_available(
                    quota_domain="quota-a",
                    now_ms=0,
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)