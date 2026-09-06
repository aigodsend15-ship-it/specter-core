# -*- coding: utf-8 -*-
import asyncio
import unittest
from pathlib import Path
import sys

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from unified_inference_gateway import (
    UnifiedInferenceGateway,
    BackendNode,
    CircuitBreaker,
    CircuitState,
    SpecterHttpServer
)

class UnifiedInferenceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = UnifiedInferenceGateway(aging_factor=1.0)
        self.node_fast = BackendNode(
            node_id="fast_local",
            base_url="http://127.0.0.1:11434",
            max_concurrency=2,
            supported_models=["qwen-coder", "llama-3"],
            is_mock=True
        )
        self.node_heavy = BackendNode(
            node_id="heavy_reasoner",
            base_url="http://127.0.0.1:8000",
            max_concurrency=1,
            supported_models=["deepseek-r1"],
            is_mock=True
        )
        self.gateway.register_node(self.node_fast)
        self.gateway.register_node(self.node_heavy)
        await self.gateway.start()

    async def asyncTearDown(self):
        await self.gateway.stop()

    async def test_synchronous_chat_completion(self):
        payload = {
            "model": "qwen-coder",
            "messages": [{"role": "user", "content": "Hello Specter"}]
        }
        res = await self.gateway.submit_request(payload, base_priority=5, stream=False)
        self.assertIn("choices", res)
        self.assertEqual(res["model"], "qwen-coder")
        self.assertEqual(res["choices"][0]["finish_reason"], "stop")

    async def test_sse_streaming_chat_completion(self):
        payload = {
            "model": "qwen-coder",
            "messages": [{"role": "user", "content": "Stream test"}]
        }
        gen = await self.gateway.submit_request(payload, base_priority=1, stream=True)
        chunks = []
        async for c in gen:
            chunks.append(c)

        self.assertGreater(len(chunks), 3)
        self.assertTrue(any("Specter" in c for c in chunks))
        self.assertTrue(any("[DONE]" in c for c in chunks))

    async def test_circuit_breaker_tripping(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.2)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.can_attempt())

        # Aguardar tempo de recuperação
        await asyncio.sleep(0.25)
        self.assertTrue(cb.can_attempt())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        cb.record_success()
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    async def test_http_server_endpoints(self):
        http_server = SpecterHttpServer(self.gateway, host="127.0.0.1", port=18088)
        await http_server.start()

        import json

        async def send_http(req_bytes: bytes) -> bytes:
            r, w = await asyncio.open_connection("127.0.0.1", 18088)
            w.write(req_bytes)
            await w.drain()
            resp = await r.read()
            w.close()
            await w.wait_closed()
            return resp

        try:
            # 1. Test GET /health
            resp1 = await send_http(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            self.assertIn(b"200 OK", resp1)
            self.assertIn(b'"status": "healthy"', resp1)

            # 2. Test GET /v1/models
            resp2 = await send_http(b"GET /v1/models HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            self.assertIn(b"200 OK", resp2)
            self.assertIn(b"qwen-coder", resp2)

            # 3. Test POST /v1/chat/completions (synchronous)
            body3 = json.dumps({
                "model": "qwen-coder",
                "messages": [{"role": "user", "content": "Ping Specter Gateway"}]
            }).encode("utf-8")
            req3 = f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {len(body3)}\r\n\r\n".encode("utf-8") + body3
            resp3 = await send_http(req3)
            self.assertIn(b"200 OK", resp3)
            self.assertIn(b"Mock reply", resp3)

            # 4. Test POST /v1/chat/completions (streaming SSE)
            body4 = json.dumps({
                "model": "qwen-coder",
                "messages": [{"role": "user", "content": "Stream test"}],
                "stream": True
            }).encode("utf-8")
            req4 = f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {len(body4)}\r\n\r\n".encode("utf-8") + body4
            resp4 = await send_http(req4)
            self.assertIn(b"200 OK", resp4)
            self.assertIn(b"text/event-stream", resp4)
            self.assertIn(b"Specter", resp4)
            self.assertIn(b"[DONE]", resp4)

        finally:
            await http_server.stop()


if __name__ == "__main__":
    unittest.main()
