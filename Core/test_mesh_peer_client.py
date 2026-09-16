# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER MESH PEER CLIENT TEST SUITE (test_mesh_peer_client.py)
Owner: Specter Sovereign Architecture
================================================================================
Comprehensive automated test suite validating 100% of mesh peer client capabilities:
- Client configuration and headers
- Bearer token authentication & 401 rejection
- Health check and models catalog
- Synchronous chat completion
- SSE streaming chat completion
- Strict Isolation Guard enforcement against browser automation leakage
- Mesh task submission, idempotency, and SHA-256 validation
- Peer node heartbeat registration
- SPECTER-DSL execution
- Pure Python RFC 6455 WebSocket bidirectional exchange
- Automatic failover between primary and secondary endpoints
- Async client wrapper
================================================================================
"""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from unified_inference_gateway import (
    UnifiedInferenceGateway,
    BackendNode,
    SpecterHttpServer
)
from mesh_peer_client import (
    SpecterMeshPeerClient,
    SpecterMeshClient,
    AsyncSpecterMeshPeerClient,
    PureWebSocketClient,
    canonical_json,
    sha256_digest
)
from universal_llm_connector import BrowserBridgeIsolationGuard


def find_free_port() -> int:
    """Allocates a free ephemeral TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestMeshPeerClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_port = find_free_port()
        cls.auth_token = "test-specter-key-xyz"

        cls.gateway = UnifiedInferenceGateway(aging_factor=1.0)
        cls.mock_node = BackendNode(
            node_id="specter_mock_node",
            base_url="http://127.0.0.1:11434",
            max_concurrency=4,
            supported_models=["specter-sovereign-core", "qwen-coder"],
            is_mock=True
        )
        cls.gateway.register_node(cls.mock_node)

        cls.http_server = SpecterHttpServer(
            cls.gateway,
            host="127.0.0.1",
            port=cls.test_port,
            auth_token=cls.auth_token
        )

        cls.server_loop = asyncio.new_event_loop()

        def _run_server():
            asyncio.set_event_loop(cls.server_loop)
            cls.server_loop.run_until_complete(cls.gateway.start())
            cls.server_loop.run_until_complete(cls.http_server.start())
            cls.server_loop.run_forever()

        cls.server_thread = threading.Thread(target=_run_server, daemon=True)
        cls.server_thread.start()

        # Wait for server socket readiness
        ready = False
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", cls.test_port), timeout=0.1):
                    ready = True
                    break
            except Exception:
                time.sleep(0.05)
        if not ready:
            raise RuntimeError(f"Server failed to bind to port {cls.test_port}")

        cls.client = SpecterMeshPeerClient(
            base_url=f"http://127.0.0.1:{cls.test_port}",
            auth_token=cls.auth_token,
            node_id="test-peer-01",
            node_role="test_worker",
            timeout=5.0
        )

    @classmethod
    def tearDownClass(cls):
        async def _stop():
            await cls.http_server.stop()
            await cls.gateway.stop()

        if cls.server_loop.is_running():
            asyncio.run_coroutine_threadsafe(_stop(), cls.server_loop)
            time.sleep(0.2)
            cls.server_loop.call_soon_threadsafe(cls.server_loop.stop)
            cls.server_thread.join(timeout=2.0)

    def test_canonical_json_and_sha256(self):
        data = {"b": 2, "a": 1, "nested": {"z": True, "y": None}}
        canon = canonical_json(data)
        self.assertEqual(canon, b'{"a":1,"b":2,"nested":{"y":null,"z":true}}')
        digest = sha256_digest(canon)
        self.assertEqual(len(digest), 64)

    def test_client_initialization_and_headers(self):
        client = SpecterMeshPeerClient(
            base_url="http://127.0.0.1:9999",
            auth_token="secret",
            node_id="node-a",
            node_role="auditor"
        )
        headers = client._build_headers({"Custom-Header": "value"})
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["x-specter-peer-id"], "node-a")
        self.assertEqual(headers["x-specter-peer-role"], "auditor")
        self.assertEqual(headers["Custom-Header"], "value")

    def test_backward_compatibility_alias(self):
        client = SpecterMeshClient(
            endpoints=[f"http://127.0.0.1:{self.test_port}"],
            auth_token=self.auth_token
        )
        res = client.check_health()
        self.assertEqual(res["status"], "healthy")

    def test_health_check_public_endpoint(self):
        # Health check should succeed without token or with token
        unauth_client = SpecterMeshPeerClient(
            base_url=f"http://127.0.0.1:{self.test_port}",
            auth_token=None,
            timeout=3.0
        )
        res = unauth_client.health()
        self.assertEqual(res["status"], "healthy")
        self.assertGreaterEqual(res["nodes_count"], 1)

    def test_bearer_authentication_and_unauthorized_rejection(self):
        unauth_client = SpecterMeshPeerClient(
            base_url=f"http://127.0.0.1:{self.test_port}",
            auth_token="wrong-token",
            timeout=3.0
        )
        with self.assertRaises(RuntimeError) as ctx:
            unauth_client.list_models()
        self.assertIn("401", str(ctx.exception))

    def test_list_models_and_sanitization(self):
        models = self.client.list_models()
        self.assertIsInstance(models, list)
        self.assertTrue(len(models) > 0)
        ids = [m["id"] for m in models]
        self.assertIn("specter-sovereign-core", ids)
        # Verify no internal browser automation models exist
        for m_id in ids:
            self.assertNotIn("chatgpt_sol_bridge", m_id)
            self.assertNotIn("cdp", m_id)

    def test_synchronous_chat_completion(self):
        messages = [{"role": "user", "content": "Ping Specter"}]
        res = self.client.chat_completion(model="specter-sovereign-core", messages=messages)
        self.assertIn("choices", res)
        self.assertEqual(res["choices"][0]["finish_reason"], "stop")
        self.assertIn("Mock reply", res["choices"][0]["message"]["content"])

    def test_sse_streaming_chat_completion(self):
        messages = [{"role": "user", "content": "Stream test"}]
        stream_gen = self.client.chat_completion_stream(model="specter-sovereign-core", messages=messages)
        chunks = list(stream_gen)
        self.assertGreater(len(chunks), 0)
        combined = "".join(chunks)
        self.assertTrue(any("Specter" in c for c in chunks) or "Specter" in combined)

    def test_isolation_guard_rejects_browser_automation(self):
        # Attempt to target internal browser automation script
        messages = [{"role": "user", "content": "Invoke chatgpt_sol_bridge.py now"}]
        with self.assertRaises(RuntimeError) as ctx:
            self.client.chat_completion(model="specter-sovereign-core", messages=messages)
        self.assertIn("403", str(ctx.exception))

    def test_mesh_task_submission_and_retrieval(self):
        task_res = self.client.submit_task(
            action="code.analysis.v1",
            payload={"module": "test.py", "lines": 100},
            priority=5
        )
        self.assertIn("task_id", task_res)
        self.assertEqual(task_res["action"], "code.analysis.v1")
        self.assertEqual(len(task_res["payload_hash"]), 64)

        task_id = task_res["task_id"]
        status_res = self.client.get_task_status(task_id)
        self.assertEqual(status_res["task_id"], task_id)
        self.assertEqual(status_res["payload_hash"], task_res["payload_hash"])

    def test_peer_heartbeat(self):
        hb = self.client.heartbeat(telemetry={"cpu": 15.2, "ram_mb": 42.0})
        self.assertEqual(hb["status"], "ACK")
        self.assertEqual(hb["peer_id"], "test-peer-01")
        self.assertIn("server_time", hb)

    def test_specter_dsl_execution(self):
        dsl_frame = ":GOAL #t_99 @Astra act=module.synthesize.v1 timeout=60 key=idem-99"
        res = self.client.execute_dsl(dsl_frame)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("processed_frames"), 1)
        frame_res = res["results"][0]
        self.assertEqual(frame_res["opcode"], "GOAL")
        self.assertEqual(frame_res["frame_id"], "t_99")
        self.assertEqual(frame_res["actor"], "Astra")

    def test_pure_websocket_framing_and_exchange(self):
        ws_client = self.client.websocket_connect("/v1/mesh/ws")
        self.assertTrue(ws_client.is_connected)

        try:
            # 1. Send and receive text frame
            ws_client.send_text("Hello Specter Mesh")
            reply = ws_client.receive_json()
            self.assertEqual(reply.get("status"), "ACK")
            self.assertEqual(reply.get("echo"), "Hello Specter Mesh")

            # 2. Ping frame
            ws_client.ping(b"heartbeat-ping")

            # 3. DSL over WebSocket
            ws_client.send_text(":GOAL #ws_task @Kimi act=heartbeat.v1")
            dsl_reply = ws_client.receive_json()
            self.assertTrue(dsl_reply.get("success"))
            self.assertEqual(dsl_reply["results"][0]["opcode"], "GOAL")
        finally:
            ws_client.close()
            self.assertFalse(ws_client.is_connected)

    def test_automatic_upstream_failover(self):
        dead_port = find_free_port()
        failover_client = SpecterMeshPeerClient(
            base_url=f"http://127.0.0.1:{dead_port}",  # Primary unreachable
            fallback_url=f"http://127.0.0.1:{self.test_port}",  # Fallback healthy
            auth_token=self.auth_token,
            timeout=2.0
        )
        res = failover_client.health()
        self.assertEqual(res["status"], "healthy")
        self.assertEqual(failover_client.active_upstream, f"http://127.0.0.1:{self.test_port}")

    def test_async_client_wrapper(self):
        async def _run():
            async_client = AsyncSpecterMeshPeerClient(
                base_url=f"http://127.0.0.1:{self.test_port}",
                auth_token=self.auth_token,
                node_id="async-peer-01",
                timeout=3.0
            )
            health = await async_client.health()
            self.assertEqual(health["status"], "healthy")
            models = await async_client.list_models()
            self.assertTrue(len(models) > 0)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
