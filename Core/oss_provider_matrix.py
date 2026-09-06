#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER MESH — OPEN-SOURCE PROVIDER MATRIX (v1.0)
Owner / Supreme Architect: Guilherme Peralta Novaes
================================================================================
Architectural Invariants:
1. Zero Commercial API Consumption:
   - 100% Open-Source backends (Ollama, HF Spaces Gradio, Generic OSS, Local Worker).
   - Zero billing, zero commercial tokens, zero vendor lock-in.
2. Ultra-low RAM Invariant (< 20 MB RSS Rest State):
   - 100% Python Standard Library (asyncio, urllib, json, hashlib, sqlite3).
   - Zero heavy ML framework imports (torch/transformers/langchain avoided in host).
3. Resilient Asynchronous Fallback & Circuit Breaker:
   - Automatic cascade across providers on node failure, timeout, or quota rejection.
   - Comprehensive provenance trail on every hop.
4. Deterministic Idempotency & SHA-256 Evidence:
   - Byte-level canonical encoding compatible with Milestone 1 Autonomous Broker.
   - Evidence manifest and SHA-256 verification.
5. High Interoperability:
   - REST and JSON-RPC 2.0 endpoints for agent swarms and autonomous brokers.
================================================================================
"""

import asyncio
from contextlib import contextmanager
import enum
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

# Default Paths & Constants
CORE_DIR = Path(r"C:\specter\Core")
CONFIG_DIR = CORE_DIR / "config"
STORAGE_DIR = CORE_DIR / "storage"
DEFAULT_DB = STORAGE_DIR / "specter_fabric.sqlite3"
SECRETS_FILE = CONFIG_DIR / "secrets.json"

DEFAULT_HF_SPACE_URL = "https://pintograndao-hermes-bridge.hf.space"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

logger = logging.getLogger("SpecterOSSMatrix")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][OSS-Matrix] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# -----------------------------------------------------------------------------
# Canonical Serialization & Cryptographic Verification (Milestone 1 Compatible)
# -----------------------------------------------------------------------------

def canonical(value: Any) -> bytes:
    """
    Deterministic canonical serialization for restricted schema.
    Strictly compatible with Milestone 1 Autonomous Broker.
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(',', ':'),
        allow_nan=False
    ).encode('ascii')


def digest(data: bytes) -> str:
    """SHA-256 cryptographic digest in hexadecimal."""
    return hashlib.sha256(data).hexdigest()


def compute_evidence_hash(manifest: Dict[str, Any]) -> str:
    """Computes Milestone 1 evidence hash with domain separation prefix."""
    return digest(b'SPECTER/EVIDENCE/v1\0' + canonical(manifest))


def load_secrets() -> Dict[str, str]:
    """Loads configuration secrets from secrets.json or environment."""
    secrets = {}
    if SECRETS_FILE.is_file():
        try:
            secrets = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to parse secrets.json: %s", e)
    # Environment variables override file
    if "HUGGINGFACE_TOKEN" in os.environ:
        secrets["HUGGINGFACE_TOKEN"] = os.environ["HUGGINGFACE_TOKEN"]
    return secrets


# -----------------------------------------------------------------------------
# Core Enums & Data Contracts
# -----------------------------------------------------------------------------

class Capability(str, enum.Enum):
    CHAT = "chat"
    GENERATE = "generate"
    CODE_EXEC = "code_exec"
    EMBEDDINGS = "embeddings"
    JSON_RPC = "json_rpc"


class ProviderType(str, enum.Enum):
    CODEX_DESKTOP = "codex_desktop"
    OLLAMA_LOCAL = "ollama_local"
    HF_SPACES = "hf_spaces"
    GENERIC_OSS = "generic_oss"
    LOCAL_WORKER = "local_worker"


class ProviderStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"


class ExecutionRequest:
    """Immutable execution request contract."""
    def __init__(
        self,
        capability: str,
        prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        code: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = 30.0,
        idempotency_key: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None
    ):
        self.request_id = request_id or uuid.uuid4().hex
        self.capability = capability
        self.prompt = prompt
        self.messages = messages or []
        self.code = code
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra = extra or {}
        
        # Calculate canonical idempotency key if omitted
        if idempotency_key:
            self.idempotency_key = idempotency_key
        else:
            payload_for_digest = {
                "capability": self.capability,
                "prompt": self.prompt,
                "messages": self.messages,
                "code": self.code,
                "model": self.model
            }
            self.idempotency_key = digest(canonical(payload_for_digest))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "capability": self.capability,
            "prompt": self.prompt,
            "messages": self.messages,
            "code": self.code,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "idempotency_key": self.idempotency_key,
            "extra": self.extra
        }

    def canonical_bytes(self) -> bytes:
        return canonical(self.to_dict())

    def spec_digest(self) -> str:
        return digest(self.canonical_bytes())


class ExecutionResponse:
    """Execution response contract with audit provenance and evidence verification."""
    def __init__(
        self,
        request_id: str,
        provider_id: str,
        provider_type: str,
        success: bool,
        output_text: Optional[str] = None,
        execution_result: Optional[Dict[str, Any]] = None,
        raw_response: Optional[Any] = None,
        error: Optional[str] = None,
        latency_ms: float = 0.0,
        provenance: Optional[List[Dict[str, Any]]] = None,
        evidence_hash: Optional[str] = None
    ):
        self.request_id = request_id
        self.provider_id = provider_id
        self.provider_type = provider_type
        self.success = success
        self.output_text = output_text
        self.execution_result = execution_result
        self.raw_response = raw_response
        self.error = error
        self.latency_ms = latency_ms
        self.provenance = provenance or []
        
        if evidence_hash:
            self.evidence_hash = evidence_hash
        else:
            manifest = {
                "request_id": self.request_id,
                "provider_id": self.provider_id,
                "provider_type": self.provider_type,
                "success": self.success,
                "error": self.error,
                "output_digest": digest(self.output_text.encode('utf-8')) if self.output_text else "",
                "result_digest": digest(canonical(self.execution_result)) if self.execution_result else "",
                "latency_ms": round(self.latency_ms, 2)
            }
            self.evidence_hash = compute_evidence_hash(manifest)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "success": self.success,
            "output_text": self.output_text,
            "execution_result": self.execution_result,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "provenance": self.provenance,
            "evidence_hash": self.evidence_hash
        }

    def canonical_bytes(self) -> bytes:
        return canonical(self.to_dict())


class ProviderHealth:
    """Snapshot of provider health, status, and telemetry."""
    def __init__(
        self,
        provider_id: str,
        status: ProviderStatus,
        is_healthy: bool,
        latency_ms: float = 0.0,
        last_checked: float = 0.0,
        consecutive_failures: int = 0,
        error_message: Optional[str] = None,
        telemetry: Optional[Dict[str, Any]] = None
    ):
        self.provider_id = provider_id
        self.status = status
        self.is_healthy = is_healthy
        self.latency_ms = latency_ms
        self.last_checked = last_checked or time.time()
        self.consecutive_failures = consecutive_failures
        self.error_message = error_message
        self.telemetry = telemetry or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status.value,
            "is_healthy": self.is_healthy,
            "latency_ms": round(self.latency_ms, 2),
            "last_checked": self.last_checked,
            "consecutive_failures": self.consecutive_failures,
            "error_message": self.error_message,
            "telemetry": self.telemetry
        }


# -----------------------------------------------------------------------------
# Base OSS Provider Specification
# -----------------------------------------------------------------------------

class BaseOSSProvider:
    """
    Abstract Open-Source Provider base class.
    Operates with zero commercial quota, asynchronous execution, and circuit breaker.
    """
    def __init__(
        self,
        provider_id: str,
        provider_type: ProviderType,
        endpoint: str,
        capabilities: Set[str],
        priority: int = 50,
        enabled: bool = True,
        timeout: float = 25.0,
        max_consecutive_failures: int = 3,
        circuit_cooldown_seconds: float = 60.0
    ):
        self.provider_id = provider_id
        self.provider_type = provider_type
        self.endpoint = endpoint.rstrip("/")
        self.capabilities = set(capabilities)
        self.priority = priority
        self.enabled = enabled
        self.timeout = timeout
        self.max_consecutive_failures = max_consecutive_failures
        self.circuit_cooldown_seconds = circuit_cooldown_seconds
        
        self.status = ProviderStatus.HEALTHY if enabled else ProviderStatus.DISABLED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self.last_checked = 0.0
        self.last_latency_ms = 0.0
        self.last_error: Optional[str] = None
        self.telemetry: Dict[str, Any] = {}

    def is_available(self) -> bool:
        """Determines if the provider is eligible for request dispatch."""
        if not self.enabled:
            return False
        if self.status == ProviderStatus.DISABLED:
            return False
        # Check circuit breaker cooldown
        if self.consecutive_failures >= self.max_consecutive_failures:
            now = time.time()
            if now - self.last_failure_time > self.circuit_cooldown_seconds:
                # Cooldown expired: allow a probe attempt (half-open state)
                return True
            return False
        return True

    def record_success(self, latency_ms: float, telemetry: Optional[Dict[str, Any]] = None):
        """Records a successful dispatch, resetting the circuit breaker."""
        self.status = ProviderStatus.HEALTHY
        self.consecutive_failures = 0
        self.last_latency_ms = latency_ms
        self.last_checked = time.time()
        self.last_error = None
        if telemetry:
            self.telemetry.update(telemetry)

    def record_failure(self, error_message: str):
        """Records a failed dispatch and updates circuit breaker state."""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        self.last_checked = self.last_failure_time
        self.last_error = error_message
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.status = ProviderStatus.OFFLINE
        else:
            self.status = ProviderStatus.DEGRADED

    async def _http_request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None
    ) -> Tuple[int, str]:
        """
        Non-blocking HTTP execution using standard library urllib and asyncio.to_thread.
        Strictly maintains zero external dependencies.
        """
        def _sync_call() -> Tuple[int, str]:
            req_headers = {
                "User-Agent": "SpecterMesh-OSSMatrix/1.0",
                "Accept": "application/json"
            }
            if headers:
                req_headers.update(headers)
            
            payload_bytes = None
            if data is not None:
                payload_bytes = json.dumps(data).encode("utf-8")
                if "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/json"

            req = urllib.request.Request(
                url=url,
                data=payload_bytes,
                headers=req_headers,
                method=method
            )
            req_timeout = timeout or self.timeout
            try:
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                    status = resp.status
                    body = resp.read().decode("utf-8", errors="replace")
                    return status, body
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                return e.code, err_body
            except Exception as e:
                raise ConnectionError(f"HTTP request to {url} failed: {e}") from e

        return await asyncio.to_thread(_sync_call)

    async def check_health(self) -> ProviderHealth:
        """Asynchronously probes provider health and latency."""
        raise NotImplementedError

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """Executes the requested capability asynchronously."""
        raise NotImplementedError


# -----------------------------------------------------------------------------
# Concrete Provider: Ollama Local Provider (0 Cost, 100% Offline / Local)
# -----------------------------------------------------------------------------

class OllamaLocalProvider(BaseOSSProvider):
    """
    Local Ollama Engine provider via standard REST API.
    Zero commercial cost, high privacy, local GPU/CPU execution.
    """
    def __init__(
        self,
        provider_id: str = "ollama_local",
        endpoint: str = DEFAULT_OLLAMA_URL,
        default_model: str = "qwen2.5-coder:latest",
        priority: int = 10,
        enabled: bool = True,
        timeout: float = 30.0
    ):
        super().__init__(
            provider_id=provider_id,
            provider_type=ProviderType.OLLAMA_LOCAL,
            endpoint=endpoint,
            capabilities={
                Capability.CHAT.value,
                Capability.GENERATE.value,
                Capability.EMBEDDINGS.value,
                Capability.JSON_RPC.value
            },
            priority=priority,
            enabled=enabled,
            timeout=timeout
        )
        self.default_model = default_model
        self.available_models: List[str] = []

    async def check_health(self) -> ProviderHealth:
        start_t = time.time()
        url = f"{self.endpoint}/api/tags"
        try:
            status, body = await self._http_request(url, method="GET", timeout=min(5.0, self.timeout))
            latency = (time.time() - start_t) * 1000.0
            if status == 200:
                data = json.loads(body)
                models = [m.get("name") for m in data.get("models", []) if "name" in m]
                self.available_models = models
                telemetry = {
                    "models_count": len(models),
                    "models": models[:10],
                    "default_model": self.default_model
                }
                self.record_success(latency, telemetry)
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status=ProviderStatus.HEALTHY,
                    is_healthy=True,
                    latency_ms=latency,
                    telemetry=telemetry
                )
            else:
                err = f"Ollama returned HTTP {status}: {body[:200]}"
                self.record_failure(err)
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status=self.status,
                    is_healthy=False,
                    latency_ms=latency,
                    error_message=err
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = str(e)
            self.record_failure(err)
            return ProviderHealth(
                provider_id=self.provider_id,
                status=self.status,
                is_healthy=False,
                latency_ms=latency,
                error_message=err
            )

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        start_t = time.time()
        model = request.model or self.default_model
        
        try:
            if request.capability in (Capability.CHAT.value, Capability.JSON_RPC.value):
                url = f"{self.endpoint}/api/chat"
                messages = request.messages
                if not messages and request.prompt:
                    messages = [{"role": "user", "content": request.prompt}]
                
                payload = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_tokens
                    }
                }
                status, body = await self._http_request(url, method="POST", data=payload, timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                
                if status == 200:
                    resp_json = json.loads(body)
                    out_text = resp_json.get("message", {}).get("content", "")
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        output_text=out_text,
                        raw_response=resp_json,
                        latency_ms=latency
                    )
                else:
                    err = f"Ollama chat error (HTTP {status}): {body[:300]}"
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )

            elif request.capability == Capability.GENERATE.value:
                url = f"{self.endpoint}/api/generate"
                payload = {
                    "model": model,
                    "prompt": request.prompt or "",
                    "stream": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_tokens
                    }
                }
                status, body = await self._http_request(url, method="POST", data=payload, timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                if status == 200:
                    resp_json = json.loads(body)
                    out_text = resp_json.get("response", "")
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        output_text=out_text,
                        raw_response=resp_json,
                        latency_ms=latency
                    )
                else:
                    err = f"Ollama generate error (HTTP {status}): {body[:300]}"
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )

            elif request.capability == Capability.EMBEDDINGS.value:
                url = f"{self.endpoint}/api/embeddings"
                payload = {
                    "model": model,
                    "prompt": request.prompt or ""
                }
                status, body = await self._http_request(url, method="POST", data=payload, timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                if status == 200:
                    resp_json = json.loads(body)
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        execution_result={"embedding": resp_json.get("embedding", [])},
                        raw_response=resp_json,
                        latency_ms=latency
                    )
                else:
                    err = f"Ollama embeddings error (HTTP {status}): {body[:300]}"
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )
            else:
                err = f"Unsupported capability '{request.capability}' for Ollama"
                return ExecutionResponse(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    provider_type=self.provider_type.value,
                    success=False,
                    error=err,
                    latency_ms=0.0
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = f"Ollama execution exception: {e}"
            self.record_failure(err)
            return ExecutionResponse(
                request_id=request.request_id,
                provider_id=self.provider_id,
                provider_type=self.provider_type.value,
                success=False,
                error=err,
                latency_ms=latency
            )


# -----------------------------------------------------------------------------
# Concrete Provider: Hugging Face Spaces (Remote 192 Cores, 0 Commercial Cost)
# -----------------------------------------------------------------------------

class HFSpacesProvider(BaseOSSProvider):
    """
    Hugging Face Spaces remote cluster node (192 Cores Linux / Hermes Bridge).
    Handles remote code execution, telemetry, and distributed task queues via Gradio SSE.
    Authenticated with HF_TOKEN for zero commercial cost community execution.
    """
    def __init__(
        self,
        provider_id: str = "hf_spaces",
        endpoint: str = DEFAULT_HF_SPACE_URL,
        hf_token: Optional[str] = None,
        priority: int = 20,
        enabled: bool = True,
        timeout: float = 40.0
    ):
        super().__init__(
            provider_id=provider_id,
            provider_type=ProviderType.HF_SPACES,
            endpoint=endpoint,
            capabilities={
                Capability.CODE_EXEC.value,
                Capability.CHAT.value,
                Capability.GENERATE.value,
                Capability.JSON_RPC.value
            },
            priority=priority,
            enabled=enabled,
            timeout=timeout
        )
        secrets = load_secrets()
        self.hf_token = hf_token or secrets.get("HUGGINGFACE_TOKEN", "")

    def _get_auth_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SpecterMesh-OSSMatrix/1.0"
        }
        if self.hf_token:
            headers["Authorization"] = f"Bearer {self.hf_token}"
        return headers

    async def _call_gradio_api(self, api_name: str, args: List[Any], timeout: float) -> Tuple[bool, Any]:
        """
        Executes a Gradio API call using Gradio's two-step protocol (POST call -> GET SSE stream).
        """
        call_url = f"{self.endpoint}/gradio_api/call/{api_name}"
        headers = self._get_auth_headers()
        
        status, body = await self._http_request(
            call_url,
            method="POST",
            data={"data": args},
            headers=headers,
            timeout=min(15.0, timeout)
        )
        
        if status != 200:
            return False, f"Gradio call initiation failed (HTTP {status}): {body[:300]}"
        
        try:
            init_res = json.loads(body)
            event_id = init_res.get("event_id")
        except Exception as e:
            return False, f"Invalid JSON in Gradio call response: {e}"

        if not event_id:
            return False, f"No event_id returned by Gradio: {body[:200]}"

        # Step 2: Read SSE event stream
        event_url = f"{call_url}/{event_id}"
        ev_status, ev_body = await self._http_request(
            event_url,
            method="GET",
            headers=headers,
            timeout=timeout
        )
        
        if ev_status != 200:
            return False, f"Gradio event stream failed (HTTP {ev_status}): {ev_body[:300]}"

        for line in ev_body.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                try:
                    payload_json = json.loads(line[5:].strip())
                    # Detect ZeroGPU quota or backend error dictionary
                    if isinstance(payload_json, dict) and "error" in payload_json:
                        return False, f"Remote Space Error: {payload_json.get('error')}"
                    # Return inner payload
                    return True, payload_json[0] if isinstance(payload_json, list) and payload_json else payload_json
                except Exception as parse_err:
                    return False, f"Failed to parse SSE payload: {parse_err}"
        
        return False, "Empty SSE stream received from Gradio endpoint"

    async def check_health(self) -> ProviderHealth:
        start_t = time.time()
        try:
            ok, result = await self._call_gradio_api("get_cluster_telemetry", [], timeout=10.0)
            latency = (time.time() - start_t) * 1000.0
            
            if ok:
                telemetry = result if isinstance(result, dict) else {"raw": result}
                self.record_success(latency, telemetry)
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status=ProviderStatus.HEALTHY,
                    is_healthy=True,
                    latency_ms=latency,
                    telemetry=telemetry
                )
            else:
                err = str(result)
                self.record_failure(err)
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status=self.status,
                    is_healthy=False,
                    latency_ms=latency,
                    error_message=err
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = f"HF Space health check exception: {e}"
            self.record_failure(err)
            return ProviderHealth(
                provider_id=self.provider_id,
                status=self.status,
                is_healthy=False,
                latency_ms=latency,
                error_message=err
            )

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        start_t = time.time()
        try:
            if request.capability == Capability.CODE_EXEC.value:
                code = request.code or request.prompt or ""
                ok, result = await self._call_gradio_api("execute_code", [code], timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                
                if ok:
                    # Result is typically JSON string containing {stdout, stderr, returncode}
                    res_dict = {}
                    if isinstance(result, str):
                        try:
                            res_dict = json.loads(result)
                        except Exception:
                            res_dict = {"stdout": result, "stderr": "", "returncode": 0}
                    elif isinstance(result, dict):
                        res_dict = result
                    
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        output_text=res_dict.get("stdout", ""),
                        execution_result=res_dict,
                        latency_ms=latency
                    )
                else:
                    err = str(result)
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )

            elif request.capability in (Capability.CHAT.value, Capability.GENERATE.value, Capability.JSON_RPC.value):
                # Remote agent task dispatch on the distributed hub
                prompt = request.prompt or (request.messages[-1]["content"] if request.messages else "")
                target_agent = request.extra.get("target_agent", "pc_agent")
                
                ok, result = await self._call_gradio_api("dispatch_agent_task", [prompt, target_agent], timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                
                if ok:
                    out_text = str(result)
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        output_text=out_text,
                        latency_ms=latency
                    )
                else:
                    err = str(result)
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )
            else:
                err = f"Capability '{request.capability}' not supported by HFSpacesProvider"
                return ExecutionResponse(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    provider_type=self.provider_type.value,
                    success=False,
                    error=err,
                    latency_ms=0.0
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = f"HF Space execution exception: {e}"
            self.record_failure(err)
            return ExecutionResponse(
                request_id=request.request_id,
                provider_id=self.provider_id,
                provider_type=self.provider_type.value,
                success=False,
                error=err,
                latency_ms=latency
            )


# -----------------------------------------------------------------------------
# Concrete Provider: Generic OSS / OpenAI-Compatible Provider (vLLM / llama.cpp)
# -----------------------------------------------------------------------------

class GenericOSSProvider(BaseOSSProvider):
    """
    OpenAI-compatible Open-Source Provider (vLLM, llama.cpp server, LocalAI, TGI).
    Zero commercial cost, standard REST & JSON-RPC schema.
    """
    def __init__(
        self,
        provider_id: str = "generic_oss",
        endpoint: str = "http://127.0.0.1:8000/v1",
        api_key: str = "EMPTY",
        default_model: str = "default",
        priority: int = 30,
        enabled: bool = False,
        timeout: float = 30.0
    ):
        super().__init__(
            provider_id=provider_id,
            provider_type=ProviderType.GENERIC_OSS,
            endpoint=endpoint,
            capabilities={
                Capability.CHAT.value,
                Capability.GENERATE.value,
                Capability.EMBEDDINGS.value,
                Capability.JSON_RPC.value
            },
            priority=priority,
            enabled=enabled,
            timeout=timeout
        )
        self.api_key = api_key
        self.default_model = default_model

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SpecterMesh-OSSMatrix/1.0"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def check_health(self) -> ProviderHealth:
        start_t = time.time()
        url = f"{self.endpoint}/models"
        try:
            status, body = await self._http_request(url, method="GET", headers=self._get_headers(), timeout=min(5.0, self.timeout))
            latency = (time.time() - start_t) * 1000.0
            if status == 200:
                data = json.loads(body)
                models = [m.get("id") for m in data.get("data", []) if "id" in m]
                telemetry = {"models": models}
                self.record_success(latency, telemetry)
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status=ProviderStatus.HEALTHY,
                    is_healthy=True,
                    latency_ms=latency,
                    telemetry=telemetry
                )
            else:
                err = f"Generic OSS returned HTTP {status}: {body[:200]}"
                self.record_failure(err)
                return ProviderHealth(
                    provider_id=self.provider_id,
                    status=self.status,
                    is_healthy=False,
                    latency_ms=latency,
                    error_message=err
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = str(e)
            self.record_failure(err)
            return ProviderHealth(
                provider_id=self.provider_id,
                status=self.status,
                is_healthy=False,
                latency_ms=latency,
                error_message=err
            )

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        start_t = time.time()
        model = request.model or self.default_model
        try:
            if request.capability in (Capability.CHAT.value, Capability.JSON_RPC.value):
                url = f"{self.endpoint}/chat/completions"
                messages = request.messages
                if not messages and request.prompt:
                    messages = [{"role": "user", "content": request.prompt}]
                
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "stream": False
                }
                status, body = await self._http_request(url, method="POST", data=payload, headers=self._get_headers(), timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                if status == 200:
                    resp_json = json.loads(body)
                    choices = resp_json.get("choices", [])
                    out_text = choices[0].get("message", {}).get("content", "") if choices else ""
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        output_text=out_text,
                        raw_response=resp_json,
                        latency_ms=latency
                    )
                else:
                    err = f"Generic OSS chat error (HTTP {status}): {body[:300]}"
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )
            elif request.capability == Capability.GENERATE.value:
                url = f"{self.endpoint}/completions"
                payload = {
                    "model": model,
                    "prompt": request.prompt or "",
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "stream": False
                }
                status, body = await self._http_request(url, method="POST", data=payload, headers=self._get_headers(), timeout=request.timeout)
                latency = (time.time() - start_t) * 1000.0
                if status == 200:
                    resp_json = json.loads(body)
                    choices = resp_json.get("choices", [])
                    out_text = choices[0].get("text", "") if choices else ""
                    self.record_success(latency)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=True,
                        output_text=out_text,
                        raw_response=resp_json,
                        latency_ms=latency
                    )
                else:
                    err = f"Generic OSS completions error (HTTP {status}): {body[:300]}"
                    self.record_failure(err)
                    return ExecutionResponse(
                        request_id=request.request_id,
                        provider_id=self.provider_id,
                        provider_type=self.provider_type.value,
                        success=False,
                        error=err,
                        latency_ms=latency
                    )
            else:
                err = f"Capability '{request.capability}' not supported by GenericOSSProvider"
                return ExecutionResponse(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    provider_type=self.provider_type.value,
                    success=False,
                    error=err,
                    latency_ms=0.0
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = f"Generic OSS execution exception: {e}"
            self.record_failure(err)
            return ExecutionResponse(
                request_id=request.request_id,
                provider_id=self.provider_id,
                provider_type=self.provider_type.value,
                success=False,
                error=err,
                latency_ms=latency
            )


# -----------------------------------------------------------------------------
# Concrete Provider: Local Deterministic Worker (Ultimate Fallback / Milestone 1)
# -----------------------------------------------------------------------------

class LocalDeterministicProvider(BaseOSSProvider):
    """
    Deterministic Local Python Execution Worker.
    Always available offline, zero RAM spike, zero external network dependency.
    Matches Milestone 1 Autonomous Broker execution model and invariants.
    """
    def __init__(
        self,
        provider_id: str = "local_worker",
        priority: int = 100,
        enabled: bool = True,
        timeout: float = 15.0
    ):
        super().__init__(
            provider_id=provider_id,
            provider_type=ProviderType.LOCAL_WORKER,
            endpoint="local://process",
            capabilities={
                Capability.CODE_EXEC.value,
                Capability.GENERATE.value,
                Capability.JSON_RPC.value
            },
            priority=priority,
            enabled=enabled,
            timeout=timeout
        )

    async def check_health(self) -> ProviderHealth:
        start_t = time.time()
        is_ok = Path(sys.executable).is_file()
        latency = (time.time() - start_t) * 1000.0
        telemetry = {
            "python_version": sys.version.split()[0],
            "executable": sys.executable,
            "platform": sys.platform
        }
        self.record_success(latency, telemetry)
        return ProviderHealth(
            provider_id=self.provider_id,
            status=ProviderStatus.HEALTHY if is_ok else ProviderStatus.OFFLINE,
            is_healthy=is_ok,
            latency_ms=latency,
            telemetry=telemetry
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        start_t = time.time()
        try:
            if request.capability == Capability.CODE_EXEC.value:
                code = request.code or request.prompt or ""
                # Execute in an isolated Python subprocess without shell
                flags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", "-c", code,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=flags
                )
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=request.timeout
                    )
                except asyncio.TimeoutError:
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
                    raise TimeoutError(f"Local worker execution timed out after {request.timeout}s")
                
                latency = (time.time() - start_t) * 1000.0
                stdout_str = stdout_bytes.decode('utf-8', errors='replace')
                stderr_str = stderr_bytes.decode('utf-8', errors='replace')
                
                exec_result = {
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "returncode": proc.returncode
                }
                
                success = (proc.returncode == 0)
                err_msg = stderr_str if not success else None
                if success:
                    self.record_success(latency)
                else:
                    self.record_failure(f"Local worker non-zero exit code: {proc.returncode}")

                return ExecutionResponse(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    provider_type=self.provider_type.value,
                    success=success,
                    output_text=stdout_str,
                    execution_result=exec_result,
                    error=err_msg,
                    latency_ms=latency
                )

            elif request.capability == Capability.GENERATE.value:
                # Deterministic text generation (echo/template/hash transform)
                text = request.prompt or ""
                latency = (time.time() - start_t) * 1000.0
                output = f"[LOCAL_DETERMINISTIC_ECHO] {text}"
                self.record_success(latency)
                return ExecutionResponse(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    provider_type=self.provider_type.value,
                    success=True,
                    output_text=output,
                    latency_ms=latency
                )
            else:
                err = f"Capability '{request.capability}' not directly supported by LocalDeterministicProvider"
                return ExecutionResponse(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    provider_type=self.provider_type.value,
                    success=False,
                    error=err,
                    latency_ms=0.0
                )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            err = f"Local worker exception: {e}"
            self.record_failure(err)
            return ExecutionResponse(
                request_id=request.request_id,
                provider_id=self.provider_id,
                provider_type=self.provider_type.value,
                success=False,
                error=err,
                latency_ms=latency
            )


# -----------------------------------------------------------------------------
# OSS Provider Matrix Router & Asynchronous Fallback Engine
# -----------------------------------------------------------------------------

class OSSProviderMatrix:
    """
    Master Router & Orchestrator for Open-Source Model and Compute Execution.
    Provides dynamic capability matching, health-based priority routing,
    automatic multi-node fallback, and JSON-RPC 2.0 interoperability.
    """
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB)
        self.providers: Dict[str, BaseOSSProvider] = {}
        self._init_persistence()

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            yield con
        finally:
            con.close()

    def _init_persistence(self):
        """Initializes audit and evidence persistence tables in SQLite WAL."""
        if not self.db_path.parent.exists():
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        try:
            with self._connection() as con:
                con.execute("PRAGMA journal_mode = WAL")
                con.execute("PRAGMA synchronous = NORMAL")
                con.execute("""
                CREATE TABLE IF NOT EXISTS oss_matrix_dispatches (
                    request_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    capability TEXT NOT NULL,
                    preferred_provider TEXT,
                    resolved_provider TEXT,
                    success INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    provenance_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """)
                con.commit()
        except Exception as e:
            logger.warning("Could not initialize matrix persistence table: %s", e)

    def register_provider(self, provider: BaseOSSProvider) -> "OSSProviderMatrix":
        """Registers an OSS provider into the routing matrix."""
        self.providers[provider.provider_id] = provider
        logger.info(
            "Registered OSS provider '%s' (type: %s, priority: %d, capabilities: %s)",
            provider.provider_id, provider.provider_type.value, provider.priority, sorted(list(provider.capabilities))
        )
        return self

    def unregister_provider(self, provider_id: str) -> Optional[BaseOSSProvider]:
        """Removes a provider from the matrix."""
        return self.providers.pop(provider_id, None)

    def get_provider(self, provider_id: str) -> Optional[BaseOSSProvider]:
        """Retrieves a provider by ID."""
        return self.providers.get(provider_id)

    def list_providers(self) -> List[Dict[str, Any]]:
        """Returns details and current status of all registered providers."""
        res = []
        for pid, p in sorted(self.providers.items(), key=lambda x: x[1].priority):
            res.append({
                "provider_id": pid,
                "provider_type": p.provider_type.value,
                "endpoint": p.endpoint,
                "capabilities": sorted(list(p.capabilities)),
                "priority": p.priority,
                "enabled": p.enabled,
                "status": p.status.value,
                "consecutive_failures": p.consecutive_failures,
                "last_latency_ms": round(p.last_latency_ms, 2),
                "is_available": p.is_available()
            })
        return res

    async def patrol_health(self) -> Dict[str, Dict[str, Any]]:
        """Runs asynchronous concurrent health checks across all registered providers."""
        tasks = []
        pids = []
        for pid, provider in self.providers.items():
            if provider.enabled:
                tasks.append(provider.check_health())
                pids.append(pid)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        report = {}
        for pid, res in zip(pids, results):
            if isinstance(res, ProviderHealth):
                report[pid] = res.to_dict()
            else:
                report[pid] = {
                    "provider_id": pid,
                    "status": ProviderStatus.OFFLINE.value,
                    "is_healthy": False,
                    "error_message": str(res)
                }
        return report

    def select_candidate_chain(
        self,
        capability: str,
        preferred_provider: Optional[str] = None
    ) -> List[BaseOSSProvider]:
        """
        Sorts eligible providers by priority and availability to form the fallback cascade.
        """
        candidates: List[BaseOSSProvider] = []
        for provider in self.providers.values():
            if not provider.enabled:
                continue
            if capability not in provider.capabilities:
                continue
            candidates.append(provider)

        # Separate candidates into available and circuit-broken
        available = [p for p in candidates if p.is_available()]
        unavailable = [p for p in candidates if not p.is_available()]

        # Sorting key: preferred provider first, then priority asc, then last_latency_ms asc
        def sort_key(p: BaseOSSProvider):
            is_pref = 0 if (preferred_provider and p.provider_id == preferred_provider) else 1
            return (is_pref, p.priority, p.last_latency_ms)

        available.sort(key=sort_key)
        # Add degraded/cooldown candidates at the very end in case all healthy nodes fail
        unavailable.sort(key=lambda p: (p.priority, p.last_latency_ms))
        
        return available + unavailable

    async def execute_with_fallback(
        self,
        request: ExecutionRequest,
        preferred_provider: Optional[str] = None,
        max_hops: Optional[int] = None
    ) -> ExecutionResponse:
        """
        Executes request with automatic asynchronous fallback.
        Tries candidates in sequence. If a node fails, records the hop and cascades.
        """
        candidates = self.select_candidate_chain(request.capability, preferred_provider)
        if not candidates:
            err = f"No eligible providers available for capability '{request.capability}'"
            logger.error(err)
            return ExecutionResponse(
                request_id=request.request_id,
                provider_id="none",
                provider_type="none",
                success=False,
                error=err
            )

        provenance: List[Dict[str, Any]] = []
        hops_allowed = max_hops or len(candidates)
        attempts = 0

        for candidate in candidates[:hops_allowed]:
            attempts += 1
            hop_record = {
                "hop": attempts,
                "provider_id": candidate.provider_id,
                "provider_type": candidate.provider_type.value,
                "started_at": time.time()
            }
            logger.info(
                "Dispatching request %s (cap: %s) to '%s' (hop %d/%d)...",
                request.request_id[:8], request.capability, candidate.provider_id, attempts, hops_allowed
            )

            try:
                response = await candidate.execute(request)
                hop_record["latency_ms"] = response.latency_ms
                hop_record["success"] = response.success
                
                if response.success:
                    hop_record["status"] = "SUCCESS"
                    provenance.append(hop_record)
                    response.provenance = provenance
                    
                    # Persist audit record
                    self._persist_dispatch(request, response, preferred_provider)
                    logger.info(
                        "Execution attained on '%s' in %.2fms", candidate.provider_id, response.latency_ms
                    )
                    return response
                else:
                    hop_record["status"] = "FAILED"
                    hop_record["error"] = response.error or "Unknown failure"
                    provenance.append(hop_record)
                    logger.warning(
                        "Hop %d on '%s' failed: %s. Initiating fallback...",
                        attempts, candidate.provider_id, hop_record["error"]
                    )
            except Exception as e:
                hop_record["status"] = "EXCEPTION"
                hop_record["error"] = str(e)
                provenance.append(hop_record)
                candidate.record_failure(str(e))
                logger.warning(
                    "Hop %d on '%s' raised exception: %s. Initiating fallback...",
                    attempts, candidate.provider_id, e
                )

        # All hops exhausted
        final_err = f"All {attempts} candidate providers failed for capability '{request.capability}'"
        final_response = ExecutionResponse(
            request_id=request.request_id,
            provider_id=candidates[0].provider_id if candidates else "none",
            provider_type="exhausted",
            success=False,
            error=final_err,
            provenance=provenance
        )
        self._persist_dispatch(request, final_response, preferred_provider)
        return final_response

    def _persist_dispatch(
        self,
        request: ExecutionRequest,
        response: ExecutionResponse,
        preferred_provider: Optional[str]
    ):
        """Durable record insertion into specter_fabric.sqlite3."""
        try:
            with self._connection() as con:
                con.execute("""
                INSERT OR REPLACE INTO oss_matrix_dispatches
                (request_id, idempotency_key, capability, preferred_provider, resolved_provider,
                 success, latency_ms, provenance_json, evidence_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    request.request_id,
                    request.idempotency_key,
                    request.capability,
                    preferred_provider or "",
                    response.provider_id,
                    1 if response.success else 0,
                    response.latency_ms,
                    json.dumps(response.provenance),
                    response.evidence_hash,
                    time.time()
                ))
                con.commit()
        except Exception as e:
            logger.debug("Failed to persist dispatch: %s", e)

    # -------------------------------------------------------------------------
    # Convenience Async Interface
    # -------------------------------------------------------------------------

    async def chat(
        self,
        prompt: str,
        messages: Optional[List[Dict[str, str]]] = None,
        model: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        timeout: float = 30.0,
        **kwargs
    ) -> ExecutionResponse:
        """Submits a chat prompt with automatic multi-node fallback."""
        req = ExecutionRequest(
            capability=Capability.CHAT.value,
            prompt=prompt,
            messages=messages,
            model=model,
            timeout=timeout,
            extra=kwargs
        )
        return await self.execute_with_fallback(req, preferred_provider=preferred_provider)

    async def execute_code(
        self,
        code: str,
        preferred_provider: Optional[str] = None,
        timeout: float = 30.0,
        **kwargs
    ) -> ExecutionResponse:
        """Executes code (remote 192 cores or local deterministic fallback)."""
        req = ExecutionRequest(
            capability=Capability.CODE_EXEC.value,
            code=code,
            timeout=timeout,
            extra=kwargs
        )
        return await self.execute_with_fallback(req, preferred_provider=preferred_provider)

    # -------------------------------------------------------------------------
    # JSON-RPC 2.0 Protocol Handler
    # -------------------------------------------------------------------------

    async def handle_json_rpc(self, rpc_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Standard JSON-RPC 2.0 dispatcher for remote tools and agent swarms.
        """
        req_id = rpc_request.get("id")
        jsonrpc_ver = rpc_request.get("jsonrpc", "2.0")
        method = rpc_request.get("method")
        params = rpc_request.get("params", {})

        def _make_error(code: int, msg: str, data: Any = None) -> Dict[str, Any]:
            err_obj: Dict[str, Any] = {"code": code, "message": msg}
            if data is not None:
                err_obj["data"] = data
            return {"jsonrpc": jsonrpc_ver, "id": req_id, "error": err_obj}

        def _make_result(result: Any) -> Dict[str, Any]:
            return {"jsonrpc": jsonrpc_ver, "id": req_id, "result": result}

        if jsonrpc_ver != "2.0" or not method:
            return _make_error(-32600, "Invalid Request: must specify jsonrpc='2.0' and method")

        try:
            if method == "matrix.status" or method == "matrix.providers":
                return _make_result(self.list_providers())

            elif method == "matrix.health":
                report = await self.patrol_health()
                return _make_result(report)

            elif method == "matrix.chat":
                prompt = params.get("prompt", "")
                messages = params.get("messages")
                preferred = params.get("preferred_provider")
                model = params.get("model")
                timeout = params.get("timeout", 30.0)
                resp = await self.chat(
                    prompt=prompt,
                    messages=messages,
                    model=model,
                    preferred_provider=preferred,
                    timeout=timeout
                )
                return _make_result(resp.to_dict())

            elif method == "matrix.execute_code":
                code = params.get("code", "")
                preferred = params.get("preferred_provider")
                timeout = params.get("timeout", 30.0)
                resp = await self.execute_code(
                    code=code,
                    preferred_provider=preferred,
                    timeout=timeout
                )
                return _make_result(resp.to_dict())

            elif method == "matrix.execute":
                req_obj = ExecutionRequest(
                    capability=params.get("capability", Capability.CHAT.value),
                    prompt=params.get("prompt"),
                    messages=params.get("messages"),
                    code=params.get("code"),
                    model=params.get("model"),
                    temperature=params.get("temperature", 0.7),
                    max_tokens=params.get("max_tokens", 2048),
                    timeout=params.get("timeout", 30.0),
                    idempotency_key=params.get("idempotency_key"),
                    extra=params.get("extra", {})
                )
                preferred = params.get("preferred_provider")
                resp = await self.execute_with_fallback(req_obj, preferred_provider=preferred)
                return _make_result(resp.to_dict())

            elif method == "matrix.set_priority":
                pid = params.get("provider_id")
                new_prio = params.get("priority")
                prov = self.get_provider(pid)
                if not prov:
                    return _make_error(-32602, f"Provider '{pid}' not found")
                prov.priority = int(new_prio)
                return _make_result({"provider_id": pid, "priority": prov.priority})

            elif method == "matrix.set_enabled":
                pid = params.get("provider_id")
                enabled = bool(params.get("enabled", True))
                prov = self.get_provider(pid)
                if not prov:
                    return _make_error(-32602, f"Provider '{pid}' not found")
                prov.enabled = enabled
                prov.status = ProviderStatus.HEALTHY if enabled else ProviderStatus.DISABLED
                return _make_result({"provider_id": pid, "enabled": prov.enabled, "status": prov.status.value})

            else:
                return _make_error(-32601, f"Method not found: {method}")

        except Exception as e:
            logger.exception("Error handling JSON-RPC method '%s'", method)
            return _make_error(-32603, f"Internal error: {str(e)}")


# -----------------------------------------------------------------------------
# Factory Initializer
# -----------------------------------------------------------------------------

def create_default_matrix(db_path: Optional[Path] = None) -> OSSProviderMatrix:
    """
    Creates an OSSProviderMatrix configured with default open-source tiers:
    1. Ollama Local Provider (priority 10)
    2. Hugging Face Spaces 192 Cores Node (priority 20)
    3. Generic OSS / OpenAI-Compatible (priority 30, disabled until configured)
    4. Local Deterministic Worker (priority 100, guaranteed fallback)
    """
    matrix = OSSProviderMatrix(db_path=db_path)
    
    # 1. Ollama Local
    ollama = OllamaLocalProvider(priority=10)
    matrix.register_provider(ollama)
    
    # 2. HF Spaces
    hf = HFSpacesProvider(priority=20)
    matrix.register_provider(hf)
    
    # 3. Generic OSS
    generic = GenericOSSProvider(priority=30, enabled=False)
    matrix.register_provider(generic)
    
    # 4. Local Deterministic
    local = LocalDeterministicProvider(priority=100)
    matrix.register_provider(local)

    # 5. Codex Desktop Local (Zero-cost local host)
    try:
        from codex_dispatch_connector import CodexDesktopProvider
        codex_prov = CodexDesktopProvider(priority=15)
        matrix.register_provider(codex_prov)
    except Exception:
        pass
    
    return matrix


if __name__ == "__main__":
    async def _cli_test():
        print("=== SPECTER MESH OSS PROVIDER MATRIX ===")
        mat = create_default_matrix()
        print("Registered Providers:")
        for p in mat.list_providers():
            print(f"  [{p['provider_id']}] Type: {p['provider_type']} | Prio: {p['priority']} | Status: {p['status']}")
        
        print("\nPatrolling Health...")
        health_report = await mat.patrol_health()
        for pid, h in health_report.items():
            print(f"  {pid}: healthy={h.get('is_healthy')} status={h.get('status')} latency={h.get('latency_ms')}ms")
        
        print("\nTesting Code Execution Fallback...")
        code = "import sys; print(f'Hello from Specter OSS Matrix! Host: {sys.platform}')"
        res = await mat.execute_code(code)
        print(f"Outcome: success={res.success} provider={res.provider_id} latency={res.latency_ms}ms")
        print(f"Output:\n{res.output_text}")
        print("Provenance hops:")
        for hop in res.provenance:
            print(f"  Hop {hop.get('hop')}: {hop.get('provider_id')} -> {hop.get('status')}")
        print(f"Evidence Hash: {res.evidence_hash}")

    asyncio.run(_cli_test())
