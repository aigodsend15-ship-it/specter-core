#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER MESH PEER CLIENT (v3.0 — LIGHTWEIGHT PURE PYTHON)
Author: Specter Sovereign Architecture & Guilherme Peralta Novaes
License: MIT
================================================================================
Zero Heavy Dependencies (100% Python Standard Library).
Connects any remote VPS, agent (OpenCode, Hermes, Claude, DeepSeek), or container
to the Specter Sovereign Mesh via REST, Server-Sent Events (SSE), and WebSocket.
================================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, AsyncGenerator, Dict, Generator, Iterator, List, Optional, Tuple, Union

__all__ = [
    "SpecterMeshPeerClient",
    "SpecterMeshClient",
    "AsyncSpecterMeshPeerClient",
    "PureWebSocketClient",
    "MeshNodeError",
    "canonical_json",
    "sha256_digest"
]

logger = logging.getLogger("SpecterMeshPeerClient")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][MeshClient] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class MeshNodeError(Exception):
    """Raised when communication with a Specter Core node fails."""
    pass


# ==============================================================================
# 1. CANONICAL CRYPTOGRAPHIC HELPERS (RFC 8785 DETERMINISM)
# ==============================================================================

def canonical_json(data: Any) -> bytes:
    """Deterministic canonical JSON serialization."""
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=True,
        separators=(',', ':'),
        allow_nan=False
    ).encode('utf-8')


def sha256_digest(data: Union[str, bytes]) -> str:
    """Computes SHA-256 hexadecimal digest."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


# ==============================================================================
# 2. PURE PYTHON RFC 6455 WEBSOCKET CLIENT
# ==============================================================================

class PureWebSocketClient:
    """
    Zero-dependency RFC 6455 compliant WebSocket client implemented
    strictly using Python standard library sockets.
    Supports ws:// and wss:// (TLS).
    """

    def __init__(
        self,
        url: str,
        auth_token: Optional[str] = None,
        timeout: float = 10.0,
        headers: Optional[Dict[str, str]] = None
    ):
        self.url = url
        self.auth_token = auth_token
        self.timeout = timeout
        self.custom_headers = headers or {}
        self.sock: Optional[socket.socket] = None
        self.is_connected = False
        self._parsed_url = urllib.parse.urlparse(url)

    def connect(self):
        """Performs TCP/TLS connection and RFC 6455 HTTP 101 Handshake."""
        scheme = self._parsed_url.scheme.lower()
        if scheme not in ("ws", "wss", "http", "https"):
            raise ValueError(f"Unsupported WebSocket scheme: {scheme}")

        is_secure = scheme in ("wss", "https")
        host = self._parsed_url.hostname or "127.0.0.1"
        port = self._parsed_url.port or (443 if is_secure else 80)
        path = self._parsed_url.path or "/"
        if self._parsed_url.query:
            path += f"?{self._parsed_url.query}"

        # 1. Connect TCP Socket
        raw_sock = socket.create_connection((host, port), timeout=self.timeout)
        if is_secure:
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        else:
            self.sock = raw_sock

        self.sock.settimeout(self.timeout)

        # 2. Generate Sec-WebSocket-Key
        sec_key = base64.b64encode(os.urandom(16)).decode('ascii')
        expected_accept = base64.b64encode(
            hashlib.sha1((sec_key + WS_GUID).encode('ascii')).digest()
        ).decode('ascii')

        # 3. Build Handshake Request
        req_lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {sec_key}",
            "Sec-WebSocket-Version: 13",
            "User-Agent: SpecterMeshPeerClient/3.0"
        ]
        if self.auth_token:
            req_lines.append(f"Authorization: Bearer {self.auth_token}")
        for k, v in self.custom_headers.items():
            req_lines.append(f"{k}: {v}")

        req_lines.extend(["", ""])
        handshake_data = "\r\n".join(req_lines).encode("utf-8")
        self.sock.sendall(handshake_data)

        # 4. Read Handshake Response
        response_bytes = bytearray()
        while b"\r\n\r\n" not in response_bytes:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("WebSocket handshake failed: connection closed prematurely")
            response_bytes.extend(chunk)

        header_part = bytes(response_bytes.split(b"\r\n\r\n", 1)[0]).decode("utf-8", errors="replace")
        lines = header_part.split("\r\n")
        status_line = lines[0]
        if "101" not in status_line:
            raise ConnectionError(f"WebSocket handshake rejected with status: {status_line}")

        headers_dict = {}
        for line in lines[1:]:
            if ":" in line:
                hk, hv = line.split(":", 1)
                headers_dict[hk.strip().lower()] = hv.strip()

        accept_val = headers_dict.get("sec-websocket-accept", "")
        if accept_val != expected_accept:
            raise ConnectionError("WebSocket handshake validation error: Sec-WebSocket-Accept mismatch")

        self.is_connected = True
        logger.info("WebSocket connected to %s", self.url)

    def _send_frame(self, opcode: int, payload: bytes):
        """Builds and sends a masked RFC 6455 frame from client to server."""
        if not self.sock or not self.is_connected:
            raise ConnectionError("WebSocket is not connected")

        b1 = 0x80 | (opcode & 0x0F)  # FIN=1 + Opcode
        length = len(payload)

        if length <= 125:
            header = bytearray([b1, 0x80 | length])
        elif length <= 65535:
            header = bytearray([b1, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytearray([b1, 0x80 | 127]) + struct.pack("!Q", length)

        mask_key = os.urandom(4)
        header.extend(mask_key)

        masked_payload = bytearray(length)
        for i in range(length):
            masked_payload[i] = payload[i] ^ mask_key[i % 4]

        self.sock.sendall(header + masked_payload)

    def _recv_exact(self, num_bytes: int) -> bytes:
        buf = bytearray()
        while len(buf) < num_bytes:
            chunk = self.sock.recv(num_bytes - len(buf))
            if not chunk:
                raise ConnectionError("Connection closed while reading frame")
            buf.extend(chunk)
        return bytes(buf)

    def _read_frame(self) -> Tuple[int, bytes]:
        """Reads an incoming RFC 6455 frame."""
        if not self.sock:
            raise ConnectionError("WebSocket is not connected")

        h1 = self._recv_exact(2)
        b1, b2 = h1[0], h1[1]

        fin = (b1 & 0x80) != 0
        opcode = b1 & 0x0F
        has_mask = (b2 & 0x80) != 0
        payload_len = b2 & 0x7F

        if payload_len == 126:
            ext = self._recv_exact(2)
            payload_len = struct.unpack("!H", ext)[0]
        elif payload_len == 127:
            ext = self._recv_exact(8)
            payload_len = struct.unpack("!Q", ext)[0]

        mask_key = None
        if has_mask:
            mask_key = self._recv_exact(4)

        payload = self._recv_exact(payload_len)

        if has_mask and mask_key:
            unmasked = bytearray(payload_len)
            for i in range(payload_len):
                unmasked[i] = payload[i] ^ mask_key[i % 4]
            payload = bytes(unmasked)

        return opcode, payload

    def send_text(self, text: str):
        """Sends a text frame (opcode 0x1)."""
        self._send_frame(0x1, text.encode("utf-8"))

    def send_json(self, data: Any):
        """Sends a JSON-encoded text frame."""
        self.send_text(json.dumps(data))

    def receive_text(self) -> str:
        """Receives a text frame, automatically handling pings/pongs/close."""
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x1:  # Text frame
                return payload.decode("utf-8")
            elif opcode == 0x9:  # Ping -> send Pong
                self._send_frame(0xA, payload)
            elif opcode == 0xA:  # Pong
                continue
            elif opcode == 0x8:  # Close frame
                self.close()
                raise ConnectionResetError("WebSocket closed by remote peer")
            else:
                logger.debug("Received frame opcode %d", opcode)

    def receive_json(self) -> Any:
        """Receives a text frame and parses it as JSON."""
        return json.loads(self.receive_text())

    def ping(self, data: bytes = b""):
        """Sends a ping control frame (opcode 0x9)."""
        self._send_frame(0x9, data)

    def close(self):
        """Sends a close frame (opcode 0x8) and closes socket."""
        if self.sock and self.is_connected:
            try:
                self._send_frame(0x8, b"")
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
        self.is_connected = False
        self.sock = None


# ==============================================================================
# 3. SPECTER MESH PEER CLIENT (REST + SSE + WS)
# ==============================================================================

class SpecterMeshPeerClient:
    """
    Lightweight, pure Python client for any remote agent or VPS
    to interact with the Specter Core Mesh.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        fallback_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        node_id: str = "peer-node-1",
        node_role: str = "specialist_worker",
        timeout: float = 30.0,
        endpoints: Optional[List[str]] = None
    ):
        if endpoints:
            self.base_url = endpoints[0].rstrip("/")
            self.fallback_url = endpoints[1].rstrip("/") if len(endpoints) > 1 else None
            self.endpoints = [e.rstrip("/") for e in endpoints]
        else:
            self.base_url = base_url.rstrip("/")
            self.fallback_url = fallback_url.rstrip("/") if fallback_url else None
            self.endpoints = [self.base_url]
            if self.fallback_url:
                self.endpoints.append(self.fallback_url)

        self.auth_token = auth_token or os.environ.get("SPECTER_AUTH_TOKEN", "specter-mesh-token-v3")
        self.node_id = node_id
        self.node_role = node_role
        self.timeout = timeout
        self.timeout_s = timeout
        self.active_upstream = self.base_url

    def _build_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SpecterMeshPeerClient/3.0",
            "x-specter-peer-id": self.node_id,
            "x-specter-peer-role": self.node_role
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if extra:
            headers.update(extra)
        return headers

    def _execute_http_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        stream: bool = False
    ) -> Any:
        """Executes HTTP request with automatic failover between primary and fallback URLs."""
        upstreams = list(self.endpoints)
        if self.active_upstream in upstreams:
            # Try active_upstream first
            upstreams.remove(self.active_upstream)
            upstreams.insert(0, self.active_upstream)

        last_error = None
        for upstream in upstreams:
            url = f"{upstream}{path}"
            headers = self._build_headers(extra_headers)
            body_bytes = json.dumps(data).encode("utf-8") if data is not None else None

            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
            try:
                if stream:
                    resp = urllib.request.urlopen(req, timeout=self.timeout)
                    self.active_upstream = upstream
                    return resp
                else:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        self.active_upstream = upstream
                        raw_data = resp.read().decode("utf-8")
                        return json.loads(raw_data) if raw_data.strip() else {}
            except urllib.error.HTTPError as e:
                # 4xx client errors should not trigger failover; only 5xx or connection errors
                if 400 <= e.code < 500:
                    err_msg = e.read().decode("utf-8", errors="replace")
                    try:
                        err_json = json.loads(err_msg)
                    except Exception:
                        err_json = {"error": {"message": err_msg, "code": e.code}}
                    raise RuntimeError(f"HTTP {e.code}: {err_json.get('error', {}).get('message', err_msg)}")
                last_error = e
                logger.warning("Upstream '%s' failed with HTTP %d. Attempting fallback...", upstream, e.code)
            except Exception as e:
                last_error = e
                logger.warning("Upstream '%s' connection failed (%s). Attempting fallback...", upstream, e)

        raise ConnectionError(f"All Specter Mesh endpoints failed. Last error: {last_error}")

    # --------------------------------------------------------------------------
    # REST API METHODS
    # --------------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        """Queries gateway health status."""
        return self._execute_http_request("GET", "/health")

    def check_health(self) -> Dict[str, Any]:
        """Alias for health()."""
        return self.health()

    def list_models(self) -> List[Dict[str, Any]]:
        """Lists available models (OpenAI catalog format)."""
        res = self._execute_http_request("GET", "/v1/models")
        return res.get("data", [])

    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        stream: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """Synchronous chat completion."""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
            **kwargs
        }
        return self._execute_http_request("POST", "/v1/chat/completions", data=payload)

    def create_chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "specter-sovereign-core",
        temperature: float = 0.7,
        stream: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """Alias compatible with SpecterMeshClient."""
        return self.chat_completion(model=model, messages=messages, temperature=temperature, stream=stream, **kwargs)

    def chat_completion_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[str, None, None]:
        """Server-Sent Events (SSE) streaming chat completion generator."""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            **kwargs
        }
        resp = self._execute_http_request(
            "POST",
            "/v1/chat/completions",
            data=payload,
            extra_headers={"Accept": "text/event-stream"},
            stream=True
        )

        try:
            for line in resp:
                decoded = line.decode("utf-8", errors="replace")
                if not decoded.strip():
                    continue
                if decoded.startswith("data: "):
                    content_str = decoded[6:].strip()
                    if content_str == "[DONE]":
                        break
                    try:
                        chunk_obj = json.loads(content_str)
                        delta = chunk_obj.get("choices", [{}])[0].get("delta", {})
                        text = delta.get("content") or delta.get("reasoning_content") or ""
                        if text:
                            yield text
                    except Exception:
                        yield content_str
        finally:
            resp.close()

    def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "specter-sovereign-core"
    ) -> Iterator[str]:
        """Alias compatible with SpecterMeshClient."""
        return self.chat_completion_stream(model=model, messages=messages)

    # --------------------------------------------------------------------------
    # MESH PEER TASK & SPECTER-DSL METHODS
    # --------------------------------------------------------------------------

    def submit_task(
        self,
        action: str,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        priority: int = 10
    ) -> Dict[str, Any]:
        """Submits an execution or analysis task to the Specter Core Mesh."""
        idem_key = idempotency_key or f"idem-{uuid.uuid4().hex[:12]}"
        req_data = {
            "action": action,
            "payload": payload,
            "idempotency_key": idem_key,
            "priority": priority,
            "peer_id": self.node_id
        }
        return self._execute_http_request("POST", "/v1/mesh/task/submit", data=req_data)

    def submit_mesh_task(
        self,
        goal: str,
        task_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Submits a goal to the mesh (compatible with SpecterMeshClient)."""
        payload = {"goal": goal, "task_id": task_id}
        return self.submit_task(action="goal.dispatch.v1", payload=payload, idempotency_key=idempotency_key)

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Fetches current execution status and cryptographic proof of a task."""
        return self._execute_http_request("GET", f"/v1/mesh/task/{task_id}")

    def heartbeat(self, telemetry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Sends node heartbeat and capabilities to the Specter Mesh Coordinator."""
        data = {
            "peer_id": self.node_id,
            "role": self.node_role,
            "telemetry": telemetry or {},
            "timestamp": time.time()
        }
        return self._execute_http_request("POST", "/v1/mesh/heartbeat", data=data)

    def execute_dsl(self, dsl_text: str) -> Dict[str, Any]:
        """Submits and compiles raw SPECTER-DSL text frames."""
        data = {
            "dsl": dsl_text,
            "peer_id": self.node_id
        }
        return self._execute_http_request("POST", "/v1/mesh/dsl", data=data)

    # --------------------------------------------------------------------------
    # WEBSOCKET CONNECTOR
    # --------------------------------------------------------------------------

    def websocket_connect(self, path: str = "/v1/mesh/ws") -> PureWebSocketClient:
        """Initializes and connects an RFC 6455 WebSocket client to the mesh gateway."""
        base = self.active_upstream.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{base}{path}"
        client = PureWebSocketClient(
            url=ws_url,
            auth_token=self.auth_token,
            timeout=self.timeout,
            headers={
                "x-specter-peer-id": self.node_id,
                "x-specter-peer-role": self.node_role
            }
        )
        client.connect()
        return client


# Alias for full backward compatibility
SpecterMeshClient = SpecterMeshPeerClient


# ==============================================================================
# 4. ASYNC CLIENT WRAPPER (ASYNCIO SUPPORT)
# ==============================================================================

class AsyncSpecterMeshPeerClient:
    """Asynchronous wrapper for SpecterMeshPeerClient executing in asyncio thread pool."""

    def __init__(self, *args, **kwargs):
        self.sync_client = SpecterMeshPeerClient(*args, **kwargs)

    async def health(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_client.health)

    async def list_models(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.sync_client.list_models)

    async def chat_completion(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_client.chat_completion, *args, **kwargs)

    async def submit_task(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_client.submit_task, *args, **kwargs)

    async def get_task_status(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_client.get_task_status, *args, **kwargs)

    async def heartbeat(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_client.heartbeat, *args, **kwargs)

    async def execute_dsl(self, *args, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_client.execute_dsl, *args, **kwargs)


# ==============================================================================
# 5. COMMAND LINE INTERFACE (CLI)
# ==============================================================================

def main_cli():
    parser = argparse.ArgumentParser(description="Specter Mesh Peer Client (Pure Python)")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Mesh Gateway base URL")
    parser.add_argument("--fallback", default=None, help="Fallback upstream URL")
    parser.add_argument("--token", default=None, help="Bearer authorization token")
    parser.add_argument("--peer-id", default=f"peer-{socket.gethostname()[:8]}", help="Peer node identifier")
    parser.add_argument("--role", default="specialist_worker", help="Peer role")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    subparsers.add_parser("health", help="Check gateway health")
    subparsers.add_parser("models", help="List available models")

    chat_p = subparsers.add_parser("chat", help="Execute chat completion")
    chat_p.add_argument("--model", default="specter-sovereign-core", help="Target model")
    chat_p.add_argument("--stream", action="store_true", help="Enable SSE streaming")
    chat_p.add_argument("prompt", help="User message prompt")

    dsl_p = subparsers.add_parser("dsl", help="Execute SPECTER-DSL frame")
    dsl_p.add_argument("frame", help="SPECTER-DSL frame text, e.g. ':GOAL #t1 @Astra act=test.v1'")

    subparsers.add_parser("heartbeat", help="Send node heartbeat")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = SpecterMeshPeerClient(
        base_url=args.url,
        fallback_url=args.fallback,
        auth_token=args.token,
        node_id=args.peer_id,
        node_role=args.role
    )

    try:
        if args.command == "health":
            res = client.health()
            print(json.dumps(res, indent=2))
        elif args.command == "models":
            res = client.list_models()
            print(json.dumps(res, indent=2))
        elif args.command == "chat":
            messages = [{"role": "user", "content": args.prompt}]
            if args.stream:
                print("Streaming response: ", flush=True)
                for chunk in client.chat_completion_stream(args.model, messages):
                    print(chunk, end="", flush=True)
                print()
            else:
                res = client.chat_completion(args.model, messages)
                content = res.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(content)
        elif args.command == "dsl":
            res = client.execute_dsl(args.frame)
            print(json.dumps(res, indent=2))
        elif args.command == "heartbeat":
            res = client.heartbeat({"cli": True, "python": sys.version.split()[0]})
            print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main_cli()
