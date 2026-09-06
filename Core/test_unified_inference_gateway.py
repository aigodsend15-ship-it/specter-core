# -*- coding: utf-8 -*-
import asyncio
import unittest
from pathlib import Path
import sys

CORE_DIR = Path(r"C:\specter\Core")
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from unified_inference_gateway import (
    UnifiedInferenceGateway,
    BackendNode,
    CircuitBreaker,
    CircuitState
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

if __name__ == "__main__":
    unittest.main()
