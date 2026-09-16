from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import threading
import time
import unittest
from collections import deque
from decimal import Decimal
from typing import Any, Callable, Optional


class MeshError(Exception):
    """Base exception for the sovereign compute mesh."""


class SchemaError(MeshError):
    """Raised when an API argument violates the mesh schema."""


class CanonicalizationError(MeshError):
    """Raised when a value cannot be represented as canonical JSON."""


class InvalidTransitionError(MeshError):
    """Raised when a task attempts an invalid FSM transition."""


class UnknownTaskError(MeshError):
    """Raised when a task identifier is unknown."""


class DuplicateTaskError(MeshError):
    """Raised when a task identifier is created more than once."""


class UnknownNodeError(MeshError):
    """Raised when a compute-node identifier is unknown."""


class TaskExecutionError(MeshError):
    """Raised when the deterministic fallback cannot complete a task."""


_SAFE_INTEGER_MAX = 9_007_199_254_740_991


def _validate_unicode_scalar_string(value: str) -> None:
    for ch in value:
        codepoint = ord(ch)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise CanonicalizationError("unpaired_surrogate")


def _utf16_sort_key(value: str) -> bytes:
    _validate_unicode_scalar_string(value)
    return value.encode("utf-16-be", "strict")


def _canonical_float(value: float) -> str:
    """
    Serialize a finite IEEE-754 double using RFC-8785/JCS-compatible
    ECMAScript presentation thresholds.

    Python's repr(float) supplies a shortest round-trippable decimal.
    Decimal is used to normalize fixed/scientific notation.
    """
    if not math.isfinite(value):
        raise CanonicalizationError("non_finite_number")

    if value == 0.0:
        return "0"

    negative = value < 0.0
    d = Decimal(repr(-value if negative else value))
    adjusted = d.adjusted()

    if -6 <= adjusted < 21:
        rendered = format(d, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
    else:
        _, digits_tuple, _ = d.as_tuple()

        digits = "".join(str(digit) for digit in digits_tuple)
        if not digits:
            digits = "0"

        while len(digits) > 1 and digits.endswith("0"):
            digits = digits[:-1]

        mantissa = digits[0]
        if len(digits) > 1:
            mantissa += "." + digits[1:]

        exponent = f"+{adjusted}" if adjusted >= 0 else str(adjusted)
        rendered = f"{mantissa}e{exponent}"

    return ("-" if negative else "") + rendered


def canonical_json_dumps(value: Any) -> str:
    """
    Deterministic canonical JSON for the RFC 8785 data model.

    Supported:
      - None
      - bool
      - str
      - IEEE-754-safe int
      - finite float
      - list
      - dict[str, ...]

    Object properties are ordered according to UTF-16 code units.
    """

    def encode(item: Any) -> str:
        if item is None:
            return "null"

        if item is True:
            return "true"

        if item is False:
            return "false"

        if isinstance(item, str):
            _validate_unicode_scalar_string(item)
            return json.dumps(
                item,
                ensure_ascii=False,
                allow_nan=False,
            )

        # bool must be checked before int because bool subclasses int.
        if isinstance(item, int):
            if abs(item) > _SAFE_INTEGER_MAX:
                raise CanonicalizationError(
                    "integer_out_of_ieee754_safe_range"
                )
            return str(item)

        if isinstance(item, float):
            return _canonical_float(item)

        if isinstance(item, list):
            return (
                "["
                + ",".join(encode(element) for element in item)
                + "]"
            )

        if isinstance(item, dict):
            for key in item:
                if not isinstance(key, str):
                    raise CanonicalizationError(
                        "non_string_object_key"
                    )
                _validate_unicode_scalar_string(key)

            ordered_keys = sorted(
                item.keys(),
                key=_utf16_sort_key,
            )

            parts = []

            for key in ordered_keys:
                encoded_key = json.dumps(
                    key,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                parts.append(
                    encoded_key + ":" + encode(item[key])
                )

            return "{" + ",".join(parts) + "}"

        raise CanonicalizationError(
            f"unsupported_json_type:{type(item).__name__}"
        )

    return encode(value)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return canonical_json_dumps(value).encode(
            "utf-8",
            "strict",
        )
    except UnicodeEncodeError as exc:
        raise CanonicalizationError(
            "invalid_utf8_scalar"
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value)
    ).hexdigest()


class SovereignComputeMesh:
    """
    Pure-Python sovereign inference-routing control plane.

    No external model is contacted directly. High-capacity nodes are logical
    execution slots whose executors are registered by the embedding runtime.

    local_worker is permanently available as a deterministic fallback.
    """

    STATES = frozenset(
        {
            "IDLE",
            "QUEUED",
            "ASSIGNED",
            "EXECUTING",
            "VERIFIED",
            "COMPLETED",
            "CIRCUIT_BROKEN",
            "FALLBACK",
        }
    )

    NODES = (
        "sol_reasoner",
        "kimi_sentinel",
        "astra_worker",
        "local_worker",
    )

    _ALLOWED_TRANSITIONS = {
        "IDLE": frozenset(
            {
                "QUEUED",
            }
        ),
        "QUEUED": frozenset(
            {
                "ASSIGNED",
                "CIRCUIT_BROKEN",
                "FALLBACK",
            }
        ),
        "ASSIGNED": frozenset(
            {
                "EXECUTING",
                "CIRCUIT_BROKEN",
                "FALLBACK",
            }
        ),
        "EXECUTING": frozenset(
            {
                "VERIFIED",
                "CIRCUIT_BROKEN",
                "FALLBACK",
            }
        ),
        "VERIFIED": frozenset(
            {
                "COMPLETED",
            }
        ),
        "COMPLETED": frozenset(),
        "CIRCUIT_BROKEN": frozenset(
            {
                "FALLBACK",
            }
        ),
        "FALLBACK": frozenset(
            {
                "ASSIGNED",
            }
        ),
    }

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        failure_window_seconds: float = 60.0,
        cooldown_seconds: float = 30.0,
        context_threshold: float = 0.85,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            not isinstance(failure_threshold, int)
            or isinstance(failure_threshold, bool)
        ):
            raise SchemaError(
                "failure_threshold_must_be_int"
            )

        if failure_threshold <= 0:
            raise SchemaError(
                "failure_threshold_must_be_positive"
            )

        if (
            not isinstance(
                failure_window_seconds,
                (int, float),
            )
            or isinstance(
                failure_window_seconds,
                bool,
            )
        ):
            raise SchemaError(
                "failure_window_seconds_must_be_number"
            )

        if (
            not isinstance(
                cooldown_seconds,
                (int, float),
            )
            or isinstance(
                cooldown_seconds,
                bool,
            )
        ):
            raise SchemaError(
                "cooldown_seconds_must_be_number"
            )

        if (
            failure_window_seconds <= 0
            or cooldown_seconds < 0
        ):
            raise SchemaError(
                "invalid_circuit_timing"
            )

        if (
            not isinstance(
                context_threshold,
                (int, float),
            )
            or isinstance(
                context_threshold,
                bool,
            )
        ):
            raise SchemaError(
                "context_threshold_must_be_number"
            )

        if not (
            0.0
            < float(context_threshold)
            <= 1.0
        ):
            raise SchemaError(
                "context_threshold_out_of_range"
            )

        if not callable(clock):
            raise SchemaError(
                "clock_must_be_callable"
            )

        self._lock = threading.RLock()
        self._clock = clock

        self._failure_threshold = (
            failure_threshold
        )
        self._failure_window_seconds = float(
            failure_window_seconds
        )
        self._cooldown_seconds = float(
            cooldown_seconds
        )
        self._context_threshold_ppm = int(
            round(
                float(context_threshold)
                * 1_000_000
            )
        )

        self._graph = {
            "sol_reasoner": {
                "role": "extended_reasoning",
                "high_capacity": True,
                "executor_registered": False,
            },
            "kimi_sentinel": {
                "role": "orchestration_planning",
                "high_capacity": True,
                "executor_registered": False,
            },
            "astra_worker": {
                "role": "local_tooling_engineering",
                "high_capacity": True,
                "executor_registered": False,
            },
            "local_worker": {
                "role": (
                    "deterministic_python_fallback"
                ),
                "high_capacity": False,
                "executor_registered": True,
            },
        }

        self._policy = {
            "failure_threshold": (
                failure_threshold
            ),
            "failure_window_ms": int(
                round(
                    self._failure_window_seconds
                    * 1000
                )
            ),
            "cooldown_ms": int(
                round(
                    self._cooldown_seconds
                    * 1000
                )
            ),
            "context_threshold_ppm": (
                self._context_threshold_ppm
            ),
            "fallback_node": "local_worker",
            "routing_order": [
                "sol_reasoner",
                "kimi_sentinel",
                "astra_worker",
                "local_worker",
            ],
        }

        self._tasks: dict[
            str,
            dict[str, Any],
        ] = {}

        self._task_payloads: dict[
            str,
            Any,
        ] = {}

        self._assignments: dict[
            str,
            str,
        ] = {}

        self._checkpoints: dict[
            str,
            dict[str, Any],
        ] = {}

        self._circuits: dict[
            str,
            dict[str, Any],
        ] = {
            node: {
                "state": "CLOSED",
                "failure_count": 0,
                "manual_available": True,
                "opened_at_us": None,
                "open_until_us": None,
                "last_failure_at_us": None,
            }
            for node in self.NODES
        }

        self._metrics = {
            "created_tasks": 0,
            "completed_tasks": 0,
            "transitions": 0,
            "compute_claims": 0,
            "node_failures": 0,
            "rate_limits": 0,
            "fallbacks": 0,
            "circuit_opens": 0,
            "circuit_recoveries": 0,
            "checkpoints": 0,
        }

        self._failure_events: dict[
            str,
            deque[float],
        ] = {
            node: deque()
            for node in self.NODES
        }

        self._context_above_threshold: dict[
            str,
            bool,
        ] = {}

        self._executors: dict[
            str,
            Callable[[Any], Any],
        ] = {
            "local_worker":
                self._deterministic_local_worker
        }

        self._transition_ledger: list[
            dict[str, Any]
        ] = []

        self._compute_claims: list[
            dict[str, Any]
        ] = []

        self._last_transition_digest: (
            Optional[str]
        ) = None

        self._last_claim_digest: (
            Optional[str]
        ) = None

        self._transition_sequence = 0
        self._claim_sequence = 0

    # -------------------------------------------------------------
    # Canonical state / integrity
    # -------------------------------------------------------------

    @staticmethod
    def canonical_bytes(
        value: Any,
    ) -> bytes:
        return canonical_json_bytes(value)

    @staticmethod
    def canonical_hash(
        value: Any,
    ) -> str:
        return canonical_sha256(value)

    def _state_material_locked(
        self,
    ) -> dict[str, Any]:
        # S_t = (G, P, T, M, A, X, C)
        return {
            "G": copy.deepcopy(
                self._graph
            ),
            "P": copy.deepcopy(
                self._policy
            ),
            "T": copy.deepcopy(
                self._tasks
            ),
            "M": copy.deepcopy(
                self._metrics
            ),
            "A": copy.deepcopy(
                self._assignments
            ),
            "X": copy.deepcopy(
                self._checkpoints
            ),
            "C": copy.deepcopy(
                self._circuits
            ),
        }

    def state_snapshot(
        self,
    ) -> dict[str, Any]:
        with self._lock:
            self._refresh_all_circuits_locked()
            return self._state_material_locked()

    def state_hash(
        self,
    ) -> str:
        with self._lock:
            self._refresh_all_circuits_locked()

            return canonical_sha256(
                self._state_material_locked()
            )

    # -------------------------------------------------------------
    # Nodes / circuit breakers
    # -------------------------------------------------------------

    def _require_node_locked(
        self,
        node: str,
    ) -> None:
        if node not in self._graph:
            raise UnknownNodeError(node)

    def register_executor(
        self,
        node: str,
        executor: Callable[[Any], Any],
    ) -> None:
        if not callable(executor):
            raise SchemaError(
                "executor_must_be_callable"
            )

        with self._lock:
            self._require_node_locked(node)

            if node == "local_worker":
                raise SchemaError(
                    "local_worker_executor_is_immutable"
                )

            self._executors[node] = executor

            self._graph[node][
                "executor_registered"
            ] = True

    def unregister_executor(
        self,
        node: str,
    ) -> None:
        with self._lock:
            self._require_node_locked(node)

            if node == "local_worker":
                raise SchemaError(
                    "local_worker_executor_is_immutable"
                )

            self._executors.pop(
                node,
                None,
            )

            self._graph[node][
                "executor_registered"
            ] = False

    def set_node_available(
        self,
        node: str,
        available: bool,
    ) -> None:
        if not isinstance(
            available,
            bool,
        ):
            raise SchemaError(
                "available_must_be_bool"
            )

        with self._lock:
            self._require_node_locked(node)

            if (
                node == "local_worker"
                and not available
            ):
                raise SchemaError(
                    "local_worker_cannot_be_disabled"
                )

            self._circuits[node][
                "manual_available"
            ] = available

    def _prune_failures_locked(
        self,
        node: str,
        now: float,
    ) -> None:
        events = self._failure_events[node]

        cutoff = (
            now
            - self._failure_window_seconds
        )

        while (
            events
            and events[0] < cutoff
        ):
            events.popleft()

        self._circuits[node][
            "failure_count"
        ] = len(events)

    def _refresh_circuit_locked(
        self,
        node: str,
    ) -> None:
        circuit = self._circuits[node]

        if circuit["state"] != "OPEN":
            self._prune_failures_locked(
                node,
                self._clock(),
            )
            return

        now = self._clock()
        now_us = int(
            now * 1_000_000
        )

        open_until_us = circuit[
            "open_until_us"
        ]

        if (
            open_until_us is not None
            and now_us >= open_until_us
        ):
            circuit["state"] = "CLOSED"
            circuit["failure_count"] = 0
            circuit["opened_at_us"] = None
            circuit["open_until_us"] = None
            circuit[
                "last_failure_at_us"
            ] = None

            self._failure_events[
                node
            ].clear()

            self._metrics[
                "circuit_recoveries"
            ] += 1

    def _refresh_all_circuits_locked(
        self,
    ) -> None:
        for node in self.NODES:
            self._refresh_circuit_locked(
                node
            )

    def _node_routable_locked(
        self,
        node: str,
    ) -> bool:
        self._refresh_circuit_locked(
            node
        )

        return (
            self._circuits[node][
                "state"
            ] == "CLOSED"
            and self._circuits[node][
                "manual_available"
            ]
            and self._graph[node][
                "executor_registered"
            ]
        )

    def is_node_available(
        self,
        node: str,
    ) -> bool:
        with self._lock:
            self._require_node_locked(node)

            return (
                self._node_routable_locked(
                    node
                )
            )

    def node_status(
        self,
        node: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_node_locked(node)

            self._refresh_circuit_locked(
                node
            )

            status = copy.deepcopy(
                self._circuits[node]
            )

            status[
                "executor_registered"
            ] = self._graph[node][
                "executor_registered"
            ]

            status["routable"] = (
                self._node_routable_locked(
                    node
                )
            )

            return status

    def _record_node_failure_locked(
        self,
        node: str,
        *,
        rate_limited: bool,
    ) -> bool:
        self._require_node_locked(node)

        now = self._clock()

        self._prune_failures_locked(
            node,
            now,
        )

        events = self._failure_events[
            node
        ]

        events.append(now)

        circuit = self._circuits[node]

        circuit["failure_count"] = (
            len(events)
        )

        circuit[
            "last_failure_at_us"
        ] = int(
            now * 1_000_000
        )

        self._metrics[
            "node_failures"
        ] += 1

        if rate_limited:
            self._metrics[
                "rate_limits"
            ] += 1

        opened_now = False

        if (
            self._graph[node][
                "high_capacity"
            ]
            and circuit["state"]
            == "CLOSED"
            and len(events)
            >= self._failure_threshold
        ):
            circuit["state"] = "OPEN"

            circuit[
                "opened_at_us"
            ] = int(
                now * 1_000_000
            )

            circuit[
                "open_until_us"
            ] = int(
                (
                    now
                    + self._cooldown_seconds
                )
                * 1_000_000
            )

            self._metrics[
                "circuit_opens"
            ] += 1

            opened_now = True

        return opened_now

    def record_node_failure(
        self,
        node: str,
        *,
        rate_limited: bool = False,
    ) -> bool:
        if not isinstance(
            rate_limited,
            bool,
        ):
            raise SchemaError(
                "rate_limited_must_be_bool"
            )

        with self._lock:
            return (
                self._record_node_failure_locked(
                    node,
                    rate_limited=rate_limited,
                )
            )

    def _record_node_success_locked(
        self,
        node: str,
    ) -> None:
        self._require_node_locked(node)

        # Success does not erase failures still inside the sliding
        # 60-second window. It only causes expired events to be pruned.
        #
        # If a breaker is OPEN, a late success from an already in-flight
        # request cannot prematurely close it.
        self._refresh_circuit_locked(
            node
        )

    # -------------------------------------------------------------
    # FSM / task lifecycle
    # -------------------------------------------------------------

    @staticmethod
    def _validate_task_id(
        task_id: str,
    ) -> None:
        if (
            not isinstance(
                task_id,
                str,
            )
            or not task_id
        ):
            raise SchemaError(
                "task_id_must_be_nonempty_str"
            )

        _validate_unicode_scalar_string(
            task_id
        )

    def _require_task_locked(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise UnknownTaskError(
                task_id
            ) from exc

    def create_task(
        self,
        task_id: str,
        payload: Any,
        *,
        preferred_node: Optional[
            str
        ] = None,
    ) -> dict[str, Any]:
        self._validate_task_id(
            task_id
        )

        payload_digest = (
            canonical_sha256(payload)
        )

        with self._lock:
            if task_id in self._tasks:
                raise DuplicateTaskError(
                    task_id
                )

            if (
                preferred_node
                is not None
            ):
                self._require_node_locked(
                    preferred_node
                )

            self._tasks[task_id] = {
                "id": task_id,
                "state": "IDLE",
                "preferred_node":
                    preferred_node,
                "assigned_node": None,
                "payload_sha256":
                    payload_digest,
                "result_sha256": None,
                "fallback_used": False,
                "attempts": 0,
                "last_error_type": None,
            }

            self._task_payloads[
                task_id
            ] = copy.deepcopy(
                payload
            )

            self._context_above_threshold[
                task_id
            ] = False

            self._metrics[
                "created_tasks"
            ] += 1

            return copy.deepcopy(
                self._tasks[task_id]
            )

    def _append_transition_proof_locked(
        self,
        *,
        task_id: str,
        from_state: str,
        to_state: str,
        node: Optional[str],
        reason: Optional[str],
    ) -> dict[str, Any]:
        self._transition_sequence += 1

        body = {
            "sequence":
                self._transition_sequence,
            "task_id": task_id,
            "from_state": from_state,
            "to_state": to_state,
            "node": node,
            "reason": reason,
            "timestamp_us": int(
                self._clock()
                * 1_000_000
            ),
            "state_sha256":
                canonical_sha256(
                    self._state_material_locked()
                ),
            "previous_transition_sha256":
                self._last_transition_digest,
        }

        digest = canonical_sha256(
            body
        )

        entry = dict(body)

        entry[
            "transition_sha256"
        ] = digest

        self._transition_ledger.append(
            entry
        )

        self._last_transition_digest = (
            digest
        )

        return entry

    def _transition_locked(
        self,
        task_id: str,
        to_state: str,
        *,
        node: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        task = self._require_task_locked(
            task_id
        )

        if to_state not in self.STATES:
            raise InvalidTransitionError(
                f"unknown_state:{to_state}"
            )

        from_state = task["state"]

        if (
            to_state
            not in self._ALLOWED_TRANSITIONS[
                from_state
            ]
        ):
            raise InvalidTransitionError(
                f"{from_state}->{to_state}"
            )

        if to_state == "ASSIGNED":
            if node is None:
                raise SchemaError(
                    "assignment_requires_node"
                )

            self._require_node_locked(
                node
            )

            task[
                "assigned_node"
            ] = node

            self._assignments[
                task_id
            ] = node

            task["attempts"] += 1

        elif node is None:
            node = task.get(
                "assigned_node"
            )

        if to_state == "FALLBACK":
            task[
                "fallback_used"
            ] = True

            self._metrics[
                "fallbacks"
            ] += 1

        task["state"] = to_state

        self._metrics[
            "transitions"
        ] += 1

        self._append_transition_proof_locked(
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            node=node,
            reason=reason,
        )

        return copy.deepcopy(task)

    def transition_task(
        self,
        task_id: str,
        to_state: str,
        *,
        node: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(
            to_state,
            str,
        ):
            raise SchemaError(
                "to_state_must_be_str"
            )

        if (
            reason is not None
            and not isinstance(
                reason,
                str,
            )
        ):
            raise SchemaError(
                "reason_must_be_str_or_none"
            )

        with self._lock:
            return self._transition_locked(
                task_id,
                to_state,
                node=node,
                reason=reason,
            )

    def get_task(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(
                self._require_task_locked(
                    task_id
                )
            )

    # -------------------------------------------------------------
    # Routing / execution
    # -------------------------------------------------------------

    @staticmethod
    def _deterministic_local_worker(
        payload: Any,
    ) -> dict[str, Any]:
        return {
            "status":
                "deterministic_fallback",
            "payload_sha256":
                canonical_sha256(payload),
        }

    def _route_locked(
        self,
        preferred_node: Optional[str],
    ) -> tuple[str, str]:
        fallback = self._policy[
            "fallback_node"
        ]

        if preferred_node is not None:
            self._require_node_locked(
                preferred_node
            )

            if preferred_node == fallback:
                return (
                    fallback,
                    "preferred_local",
                )

            if self._node_routable_locked(
                preferred_node
            ):
                return (
                    preferred_node,
                    "preferred_available",
                )

            if (
                self._circuits[
                    preferred_node
                ]["state"]
                == "OPEN"
            ):
                return (
                    fallback,
                    "preferred_circuit_open",
                )

            return (
                fallback,
                "preferred_unavailable",
            )

        for node in self._policy[
            "routing_order"
        ]:
            if node == fallback:
                continue

            if self._node_routable_locked(
                node
            ):
                return (
                    node,
                    "ordered_available",
                )

        return (
            fallback,
            "deterministic_fallback",
        )

    def route_node(
        self,
        preferred_node: Optional[
            str
        ] = None,
    ) -> str:
        with self._lock:
            node, _ = self._route_locked(
                preferred_node
            )

            return node

    def _assign_for_dispatch_locked(
        self,
        task_id: str,
        preferred_node: Optional[str],
    ) -> str:
        node, reason = self._route_locked(
            preferred_node
        )

        fallback = self._policy[
            "fallback_node"
        ]

        if (
            node == fallback
            and preferred_node
            not in (
                None,
                fallback,
            )
        ):
            if (
                reason
                == "preferred_circuit_open"
            ):
                self._transition_locked(
                    task_id,
                    "CIRCUIT_BROKEN",
                    node=preferred_node,
                    reason=reason,
                )

                self._transition_locked(
                    task_id,
                    "FALLBACK",
                    node=fallback,
                    reason=(
                        "atomic_fallback_after_circuit"
                    ),
                )

            else:
                self._transition_locked(
                    task_id,
                    "FALLBACK",
                    node=fallback,
                    reason=reason,
                )

        elif (
            node == fallback
            and preferred_node
            is None
        ):
            self._transition_locked(
                task_id,
                "FALLBACK",
                node=fallback,
                reason=reason,
            )

        self._transition_locked(
            task_id,
            "ASSIGNED",
            node=node,
            reason=reason,
        )

        return node

    def _append_compute_claim_locked(
        self,
        *,
        task_id: str,
        node: str,
        input_digest: str,
        output_digest: str,
    ) -> dict[str, Any]:
        self._claim_sequence += 1

        self._metrics[
            "compute_claims"
        ] += 1

        body = {
            "sequence":
                self._claim_sequence,
            "task_id": task_id,
            "node": node,
            "input_sha256":
                input_digest,
            "output_sha256":
                output_digest,
            "timestamp_us": int(
                self._clock()
                * 1_000_000
            ),
            "state_sha256":
                canonical_sha256(
                    self._state_material_locked()
                ),
            "previous_claim_sha256":
                self._last_claim_digest,
        }

        digest = canonical_sha256(
            body
        )

        claim = dict(body)

        claim[
            "claim_sha256"
        ] = digest

        self._compute_claims.append(
            claim
        )

        self._last_claim_digest = (
            digest
        )

        return claim

    def _fallback_after_failure_locked(
        self,
        task_id: str,
        failed_node: str,
        *,
        breaker_opened: bool,
        error_type: str,
    ) -> str:
        task = self._require_task_locked(
            task_id
        )

        task[
            "last_error_type"
        ] = error_type

        fallback = self._policy[
            "fallback_node"
        ]

        if breaker_opened:
            self._transition_locked(
                task_id,
                "CIRCUIT_BROKEN",
                node=failed_node,
                reason=(
                    "failure_threshold_reached"
                ),
            )

            self._transition_locked(
                task_id,
                "FALLBACK",
                node=fallback,
                reason=(
                    "atomic_fallback_after_breaker_open"
                ),
            )

        else:
            self._transition_locked(
                task_id,
                "FALLBACK",
                node=fallback,
                reason=(
                    "execution_failure"
                ),
            )

        self._transition_locked(
            task_id,
            "ASSIGNED",
            node=fallback,
            reason=(
                "deterministic_retry"
            ),
        )

        return fallback

    def _execute_task(
        self,
        task_id: str,
        node: str,
    ) -> dict[str, Any]:
        while True:
            with self._lock:
                task = (
                    self._require_task_locked(
                        task_id
                    )
                )

                self._transition_locked(
                    task_id,
                    "EXECUTING",
                    node=node,
                )

                payload = copy.deepcopy(
                    self._task_payloads[
                        task_id
                    ]
                )

                executor = (
                    self._executors.get(
                        node
                    )
                )

                if executor is None:
                    if node == "local_worker":
                        raise TaskExecutionError(
                            "local_worker_executor_missing"
                        )

                    breaker_opened = (
                        self._record_node_failure_locked(
                            node,
                            rate_limited=False,
                        )
                    )

                    node = (
                        self._fallback_after_failure_locked(
                            task_id,
                            node,
                            breaker_opened=(
                                breaker_opened
                            ),
                            error_type=(
                                "executor_missing"
                            ),
                        )
                    )

                    continue

            # Arbitrary executor code runs with no mesh lock held.
            #
            # Consequences:
            # - executor can re-enter mesh methods safely
            # - a slow model does not block routing
            # - breaker activation does not cancel already in-flight tasks
            # - lock-order inversion is avoided
            try:
                result = executor(
                    payload
                )

                output_digest = (
                    canonical_sha256(
                        result
                    )
                )

            except Exception as exc:
                if node == "local_worker":
                    raise TaskExecutionError(
                        "local_worker_failed:"
                        f"{type(exc).__name__}"
                    ) from exc

                with self._lock:
                    breaker_opened = (
                        self._record_node_failure_locked(
                            node,
                            rate_limited=False,
                        )
                    )

                    node = (
                        self._fallback_after_failure_locked(
                            task_id,
                            node,
                            breaker_opened=(
                                breaker_opened
                            ),
                            error_type=(
                                type(exc).__name__
                            ),
                        )
                    )

                continue

            with self._lock:
                task = (
                    self._require_task_locked(
                        task_id
                    )
                )

                task[
                    "result_sha256"
                ] = output_digest

                task[
                    "last_error_type"
                ] = None

                input_digest = task[
                    "payload_sha256"
                ]

                self._record_node_success_locked(
                    node
                )

                claim = (
                    self._append_compute_claim_locked(
                        task_id=task_id,
                        node=node,
                        input_digest=(
                            input_digest
                        ),
                        output_digest=(
                            output_digest
                        ),
                    )
                )

                self._transition_locked(
                    task_id,
                    "VERIFIED",
                    node=node,
                )

                self._metrics[
                    "completed_tasks"
                ] += 1

                self._transition_locked(
                    task_id,
                    "COMPLETED",
                    node=node,
                )

                final_task = (
                    copy.deepcopy(task)
                )

            return {
                "task": final_task,
                "result": result,
                "claim":
                    copy.deepcopy(
                        claim
                    ),
            }

    def dispatch(
        self,
        task_id: str,
        payload: Any,
        *,
        preferred_node: Optional[
            str
        ] = None,
    ) -> dict[str, Any]:
        self.create_task(
            task_id,
            payload,
            preferred_node=(
                preferred_node
            ),
        )

        with self._lock:
            self._transition_locked(
                task_id,
                "QUEUED",
            )

            node = (
                self._assign_for_dispatch_locked(
                    task_id,
                    preferred_node,
                )
            )

        return self._execute_task(
            task_id,
            node,
        )

    # -------------------------------------------------------------
    # Context saturation / checkpoints
    # -------------------------------------------------------------

    def monitor_context(
        self,
        task_id: str,
        used_tokens: int,
        limit_tokens: int,
        *,
        summary: str = "",
    ) -> Optional[dict[str, Any]]:
        if (
            not isinstance(
                used_tokens,
                int,
            )
            or isinstance(
                used_tokens,
                bool,
            )
        ):
            raise SchemaError(
                "used_tokens_must_be_int"
            )

        if (
            not isinstance(
                limit_tokens,
                int,
            )
            or isinstance(
                limit_tokens,
                bool,
            )
        ):
            raise SchemaError(
                "limit_tokens_must_be_int"
            )

        if (
            used_tokens < 0
            or limit_tokens <= 0
        ):
            raise SchemaError(
                "invalid_token_counts"
            )

        if not isinstance(
            summary,
            str,
        ):
            raise SchemaError(
                "summary_must_be_str"
            )

        _validate_unicode_scalar_string(
            summary
        )

        with self._lock:
            task = (
                self._require_task_locked(
                    task_id
                )
            )

            threshold_reached = (
                used_tokens
                * 1_000_000
                >= limit_tokens
                * self._context_threshold_ppm
            )

            was_above = (
                self._context_above_threshold.get(
                    task_id,
                    False,
                )
            )

            if not threshold_reached:
                self._context_above_threshold[
                    task_id
                ] = False

                return None

            if was_above:
                return None

            self._context_above_threshold[
                task_id
            ] = True

            utilization_ppm = (
                used_tokens
                * 1_000_000
            ) // limit_tokens

            compact_summary = (
                summary[:512]
            )

            body = {
                "version": 1,
                "task_id": task_id,
                "task_state":
                    task["state"],
                "used_tokens":
                    used_tokens,
                "limit_tokens":
                    limit_tokens,
                "utilization_ppm":
                    utilization_ppm,
                "threshold_ppm":
                    self._context_threshold_ppm,
                "summary":
                    compact_summary,
                "mesh_state_sha256":
                    canonical_sha256(
                        self._state_material_locked()
                    ),
            }

            digest = canonical_sha256(
                body
            )

            checkpoint = dict(body)

            checkpoint[
                "checkpoint_sha256"
            ] = digest

            self._checkpoints[
                task_id
            ] = copy.deepcopy(
                checkpoint
            )

            self._metrics[
                "checkpoints"
            ] += 1

            return checkpoint

    @staticmethod
    def validate_checkpoint(
        checkpoint: dict[str, Any],
    ) -> bool:
        if not isinstance(
            checkpoint,
            dict,
        ):
            return False

        required = {
            "version",
            "task_id",
            "task_state",
            "used_tokens",
            "limit_tokens",
            "utilization_ppm",
            "threshold_ppm",
            "summary",
            "mesh_state_sha256",
            "checkpoint_sha256",
        }

        if (
            set(checkpoint.keys())
            != required
        ):
            return False

        checksum = checkpoint.get(
            "checkpoint_sha256"
        )

        if not isinstance(
            checksum,
            str,
        ):
            return False

        body = dict(checkpoint)

        del body[
            "checkpoint_sha256"
        ]

        try:
            expected = (
                canonical_sha256(
                    body
                )
            )
        except CanonicalizationError:
            return False

        return hmac.compare_digest(
            expected,
            checksum,
        )

    # -------------------------------------------------------------
    # Integrity ledgers / observability
    # -------------------------------------------------------------

    def transition_ledger(
        self,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(
                self._transition_ledger
            )

    def compute_claims(
        self,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(
                self._compute_claims
            )

    def metrics(
        self,
    ) -> dict[str, int]:
        with self._lock:
            return copy.deepcopy(
                self._metrics
            )

    def verify_transition_ledger(
        self,
    ) -> bool:
        with self._lock:
            previous: Optional[
                str
            ] = None

            expected_sequence = 1

            for entry in (
                self._transition_ledger
            ):
                if (
                    entry.get(
                        "sequence"
                    )
                    != expected_sequence
                ):
                    return False

                if (
                    entry.get(
                        "previous_transition_sha256"
                    )
                    != previous
                ):
                    return False

                digest = entry.get(
                    "transition_sha256"
                )

                if not isinstance(
                    digest,
                    str,
                ):
                    return False

                body = dict(entry)

                del body[
                    "transition_sha256"
                ]

                if not hmac.compare_digest(
                    canonical_sha256(
                        body
                    ),
                    digest,
                ):
                    return False

                previous = digest
                expected_sequence += 1

            return True

    def verify_compute_claims(
        self,
    ) -> bool:
        with self._lock:
            previous: Optional[
                str
            ] = None

            expected_sequence = 1

            for claim in (
                self._compute_claims
            ):
                if (
                    claim.get(
                        "sequence"
                    )
                    != expected_sequence
                ):
                    return False

                if (
                    claim.get(
                        "previous_claim_sha256"
                    )
                    != previous
                ):
                    return False

                digest = claim.get(
                    "claim_sha256"
                )

                if not isinstance(
                    digest,
                    str,
                ):
                    return False

                body = dict(claim)

                del body[
                    "claim_sha256"
                ]

                if not hmac.compare_digest(
                    canonical_sha256(
                        body
                    ),
                    digest,
                ):
                    return False

                previous = digest
                expected_sequence += 1

            return True


class _FakeClock:
    def __init__(
        self,
        start: float = 1_700_000_000.0,
    ) -> None:
        self._value = start
        self._lock = threading.Lock()

    def __call__(
        self,
    ) -> float:
        with self._lock:
            return self._value

    def advance(
        self,
        seconds: float,
    ) -> None:
        with self._lock:
            self._value += seconds


class TestSovereignComputeMesh(
    unittest.TestCase
):
    def test_canonical_creation_and_deterministic_hash(
        self,
    ) -> None:
        left = {
            "z": [3, 2, 1],
            "a": {
                "x": 1,
                "y": 1.0,
            },
        }

        right = {
            "a": {
                "y": 1.0,
                "x": 1,
            },
            "z": [3, 2, 1],
        }

        self.assertEqual(
            canonical_json_bytes(
                left
            ),
            canonical_json_bytes(
                right
            ),
        )

        self.assertEqual(
            canonical_sha256(
                left
            ),
            canonical_sha256(
                right
            ),
        )

        self.assertEqual(
            len(
                canonical_sha256(
                    left
                )
            ),
            64,
        )

        mesh_a = (
            SovereignComputeMesh()
        )

        mesh_b = (
            SovereignComputeMesh()
        )

        self.assertEqual(
            mesh_a.state_hash(),
            mesh_b.state_hash(),
        )

        self.assertEqual(
            set(
                mesh_a
                .state_snapshot()
                .keys()
            ),
            {
                "G",
                "P",
                "T",
                "M",
                "A",
                "X",
                "C",
            },
        )

    def test_valid_fsm_transitions_and_invalid_transition_rejected(
        self,
    ) -> None:
        mesh = (
            SovereignComputeMesh()
        )

        mesh.create_task(
            "fsm-1",
            {
                "work": 1,
            },
        )

        self.assertEqual(
            mesh.get_task(
                "fsm-1"
            )["state"],
            "IDLE",
        )

        mesh.transition_task(
            "fsm-1",
            "QUEUED",
        )

        mesh.transition_task(
            "fsm-1",
            "ASSIGNED",
            node="local_worker",
        )

        mesh.transition_task(
            "fsm-1",
            "EXECUTING",
        )

        mesh.transition_task(
            "fsm-1",
            "VERIFIED",
        )

        mesh.transition_task(
            "fsm-1",
            "COMPLETED",
        )

        self.assertEqual(
            mesh.get_task(
                "fsm-1"
            )["state"],
            "COMPLETED",
        )

        self.assertTrue(
            mesh.verify_transition_ledger()
        )

        self.assertEqual(
            len(
                mesh.transition_ledger()
            ),
            5,
        )

        with self.assertRaises(
            InvalidTransitionError
        ):
            mesh.transition_task(
                "fsm-1",
                "QUEUED",
            )

    def test_concurrent_dispatch_under_one_hundred_threads(
        self,
    ) -> None:
        mesh = (
            SovereignComputeMesh()
        )

        def sol_executor(
            payload: Any,
        ) -> dict[str, Any]:
            # Re-enter the mesh from executor code.
            # This would deadlock if dispatch held the RLock.
            _ = mesh.state_hash()

            time.sleep(
                0.001
            )

            return {
                "value":
                    payload["value"]
                    * 2
            }

        mesh.register_executor(
            "sol_reasoner",
            sol_executor,
        )

        total = 100

        barrier = (
            threading.Barrier(
                total
            )
        )

        results: dict[
            int,
            dict[str, Any],
        ] = {}

        errors: list[
            BaseException
        ] = []

        result_lock = (
            threading.Lock()
        )

        def runner(
            index: int,
        ) -> None:
            try:
                barrier.wait(
                    timeout=5
                )

                result = (
                    mesh.dispatch(
                        f"concurrent-{index}",
                        {
                            "value":
                                index
                        },
                        preferred_node=(
                            "sol_reasoner"
                        ),
                    )
                )

                with result_lock:
                    results[
                        index
                    ] = result

            except BaseException as exc:
                with result_lock:
                    errors.append(
                        exc
                    )

        threads = [
            threading.Thread(
                target=runner,
                args=(i,),
            )
            for i in range(total)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(
                timeout=10
            )

        self.assertFalse(
            any(
                thread.is_alive()
                for thread
                in threads
            )
        )

        self.assertEqual(
            errors,
            [],
        )

        self.assertEqual(
            len(results),
            total,
        )

        self.assertTrue(
            all(
                item["task"][
                    "state"
                ]
                == "COMPLETED"
                for item
                in results.values()
            )
        )

        self.assertTrue(
            all(
                item["task"][
                    "assigned_node"
                ]
                == "sol_reasoner"
                for item
                in results.values()
            )
        )

        self.assertEqual(
            mesh.metrics()[
                "completed_tasks"
            ],
            total,
        )

        self.assertTrue(
            mesh.verify_transition_ledger()
        )

        self.assertTrue(
            mesh.verify_compute_claims()
        )

    def test_circuit_breaker_activation_and_automatic_recovery(
        self,
    ) -> None:
        clock = _FakeClock()

        mesh = (
            SovereignComputeMesh(
                failure_threshold=3,
                failure_window_seconds=60,
                cooldown_seconds=5,
                clock=clock,
            )
        )

        def failing_executor(
            payload: Any,
        ) -> Any:
            raise RuntimeError(
                "simulated"
            )

        mesh.register_executor(
            "sol_reasoner",
            failing_executor,
        )

        for index in range(3):
            result = (
                mesh.dispatch(
                    f"breaker-{index}",
                    {
                        "index":
                            index
                    },
                    preferred_node=(
                        "sol_reasoner"
                    ),
                )
            )

            self.assertEqual(
                result["task"][
                    "state"
                ],
                "COMPLETED",
            )

            self.assertTrue(
                result["task"][
                    "fallback_used"
                ]
            )

        status = (
            mesh.node_status(
                "sol_reasoner"
            )
        )

        self.assertEqual(
            status["state"],
            "OPEN",
        )

        self.assertEqual(
            status[
                "failure_count"
            ],
            3,
        )

        self.assertFalse(
            status["routable"]
        )

        self.assertGreaterEqual(
            mesh.metrics()[
                "circuit_opens"
            ],
            1,
        )

        clock.advance(
            6
        )

        recovered = (
            mesh.node_status(
                "sol_reasoner"
            )
        )

        self.assertEqual(
            recovered["state"],
            "CLOSED",
        )

        self.assertTrue(
            recovered["routable"]
        )

        self.assertEqual(
            recovered[
                "failure_count"
            ],
            0,
        )

        mesh.register_executor(
            "sol_reasoner",
            lambda payload: {
                "ok":
                    payload["ok"]
            },
        )

        result = mesh.dispatch(
            "breaker-recovered",
            {
                "ok": True,
            },
            preferred_node=(
                "sol_reasoner"
            ),
        )

        self.assertEqual(
            result["task"][
                "assigned_node"
            ],
            "sol_reasoner",
        )

        self.assertFalse(
            result["task"][
                "fallback_used"
            ]
        )

    def test_deterministic_fallback_when_preferred_node_unavailable(
        self,
    ) -> None:
        mesh = (
            SovereignComputeMesh()
        )

        mesh.register_executor(
            "sol_reasoner",
            lambda payload: {
                "should_not_run":
                    True
            },
        )

        mesh.set_node_available(
            "sol_reasoner",
            False,
        )

        result = mesh.dispatch(
            "fallback-1",
            {
                "payload": "x",
            },
            preferred_node=(
                "sol_reasoner"
            ),
        )

        self.assertEqual(
            result["task"][
                "state"
            ],
            "COMPLETED",
        )

        self.assertEqual(
            result["task"][
                "assigned_node"
            ],
            "local_worker",
        )

        self.assertTrue(
            result["task"][
                "fallback_used"
            ]
        )

        self.assertEqual(
            result["result"][
                "status"
            ],
            "deterministic_fallback",
        )

        self.assertEqual(
            result["result"][
                "payload_sha256"
            ],
            canonical_sha256(
                {
                    "payload": "x",
                }
            ),
        )

        self.assertEqual(
            result["claim"][
                "node"
            ],
            "local_worker",
        )

    def test_context_saturation_checkpoint_emission_and_validation(
        self,
    ) -> None:
        mesh = (
            SovereignComputeMesh(
                context_threshold=0.85
            )
        )

        mesh.create_task(
            "ctx-1",
            {
                "prompt": "x",
            },
        )

        self.assertIsNone(
            mesh.monitor_context(
                "ctx-1",
                849,
                1000,
            )
        )

        checkpoint = (
            mesh.monitor_context(
                "ctx-1",
                850,
                1000,
                summary=(
                    "compact checkpoint"
                ),
            )
        )

        self.assertIsNotNone(
            checkpoint
        )

        assert checkpoint is not None

        self.assertEqual(
            checkpoint[
                "utilization_ppm"
            ],
            850_000,
        )

        self.assertTrue(
            mesh.validate_checkpoint(
                checkpoint
            )
        )

        self.assertEqual(
            len(
                checkpoint[
                    "checkpoint_sha256"
                ]
            ),
            64,
        )

        tampered = dict(
            checkpoint
        )

        tampered[
            "used_tokens"
        ] = 851

        self.assertFalse(
            mesh.validate_checkpoint(
                tampered
            )
        )

        # Edge-triggered checkpointing prevents a checkpoint storm.
        self.assertIsNone(
            mesh.monitor_context(
                "ctx-1",
                900,
                1000,
            )
        )

        # Going below the threshold rearms the monitor.
        self.assertIsNone(
            mesh.monitor_context(
                "ctx-1",
                500,
                1000,
            )
        )

        checkpoint_2 = (
            mesh.monitor_context(
                "ctx-1",
                900,
                1000,
            )
        )

        self.assertIsNotNone(
            checkpoint_2
        )

        self.assertEqual(
            mesh.metrics()[
                "checkpoints"
            ],
            2,
        )


if __name__ == "__main__":
    unittest.main()