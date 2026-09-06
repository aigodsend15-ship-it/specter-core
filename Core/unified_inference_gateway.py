# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER UNIFIED INFERENCE GATEWAY (v1.0 - PRODUCTION CORE)
Architected by: Aether (Theory), Nyx (Protocols), Helios (Orch), Forge (Engine)
================================================================================
Properties:
1. Standard OpenAI Protocol (/v1/chat/completions + text/event-stream SSE).
2. BackendNode abstraction with dynamic health checks and concurrency leases.
3. Adaptive Circuit Breaker (CLOSED -> OPEN -> HALF_OPEN).
4. Priority queue with aging to eliminate starvation: p_eff = p0 + alpha * delta_t.
5. Zero mandatory external ML packages (asyncio + standard library HTTP server).
================================================================================
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import time
import uuid
import urllib.request
import urllib.error
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

logger = logging.getLogger("SpecterGateway")
if not logger.handlers:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][Gateway] %(message)s"))
    logger.addHandler(sh)
    logger.setLevel(logging.INFO)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    half_open_success_threshold: int = 2

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.half_open_success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info("Circuit Breaker re-CLOSED (recovered)")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self):
        self.last_failure_time = time.time()
        self.failure_count += 1
        if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning("Circuit Breaker transitioned to OPEN (failing fast)")

    def can_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info("Circuit Breaker transitioned to HALF_OPEN (probing)")
                return True
            return False
        return True


@dataclass(order=True)
class PrioritizedRequest:
    effective_priority: float
    created_at: float = field(compare=False)
    request_id: str = field(compare=False)
    payload: Dict[str, Any] = field(compare=False)
    future: asyncio.Future = field(compare=False)
    is_stream: bool = field(compare=False, default=False)


class BackendNode:
    """Representa um nó físico ou virtual executando modelo compatível com OpenAI."""
    def __init__(
        self,
        node_id: str,
        base_url: str,
        max_concurrency: int = 4,
        supported_models: Optional[List[str]] = None,
        is_mock: bool = False
    ):
        self.node_id = node_id
        self.base_url = base_url.rstrip("/")
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.supported_models = supported_models or ["*"]
        self.circuit = CircuitBreaker()
        self.is_mock = is_mock
        self.total_requests = 0
        self.total_tokens_emitted = 0
        self.total_latency_ms = 0.0

    def supports_model(self, model_name: str) -> bool:
        if "*" in self.supported_models:
            return True
        return model_name in self.supported_models

    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execução síncrona consolidada."""
        if not self.circuit.can_attempt():
            raise RuntimeError(f"Node '{self.node_id}' circuit is OPEN")

        start = time.time()
        async with self.semaphore:
            try:
                if self.is_mock:
                    await asyncio.sleep(0.02)
                    content = f"Mock reply from {self.node_id} for model {payload.get('model')}"
                    res = {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": payload.get("model", "default"),
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop"
                        }],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25}
                    }
                else:
                    url = f"{self.base_url}/v1/chat/completions"
                    data_bytes = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        res = json.loads(resp.read().decode("utf-8"))

                latency = (time.time() - start) * 1000
                self.total_latency_ms += latency
                self.total_requests += 1
                self.circuit.record_success()
                return res
            except Exception as e:
                self.circuit.record_failure()
                raise e

    async def execute_stream(self, payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Execução com streaming Server-Sent Events (SSE)."""
        if not self.circuit.can_attempt():
            raise RuntimeError(f"Node '{self.node_id}' circuit is OPEN")

        async with self.semaphore:
            try:
                if self.is_mock:
                    words = ["Specter", "Unified", "Gateway", "operational", "evidence."]
                    req_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                    model_name = payload.get("model", "default")

                    for w in words:
                        await asyncio.sleep(0.01)
                        chunk = {
                            "id": req_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_name,
                            "choices": [{"index": 0, "delta": {"content": w + " "}, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"

                    done_chunk = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(done_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    self.circuit.record_success()
                else:
                    # Em nós reais remotos com streaming HTTP
                    payload["stream"] = True
                    url = f"{self.base_url}/v1/chat/completions"
                    data_bytes = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        for line in resp:
                            decoded = line.decode("utf-8")
                            if decoded.strip():
                                yield decoded
                    self.circuit.record_success()
            except Exception as e:
                self.circuit.record_failure()
                raise e


class UnifiedInferenceGateway:
    """Núcleo coordenador de inferência distribuída."""
    def __init__(self, aging_factor: float = 0.5):
        self.nodes: Dict[str, BackendNode] = {}
        self.priority_queue = asyncio.PriorityQueue()
        self.aging_factor = aging_factor  # alpha para anti-starvation
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    def register_node(self, node: BackendNode):
        self.nodes[node.node_id] = node
        logger.info("Registrado backend node: %s (models: %s)", node.node_id, node.supported_models)

    def select_healthy_node(self, model: str) -> Optional[BackendNode]:
        """Seleção por afinidade de modelo e disponibilidade de circuito."""
        candidates = [
            n for n in self.nodes.values()
            if n.supports_model(model) and n.circuit.can_attempt()
        ]
        if not candidates:
            return None
        # Balanceamento por menor concorrência ativa
        return min(candidates, key=lambda n: n.semaphore._value if hasattr(n.semaphore, "_value") else 0)

    async def submit_request(
        self,
        payload: Dict[str, Any],
        base_priority: int = 10,
        stream: bool = False
    ) -> Any:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        now = time.time()
        req_id = f"req-{uuid.uuid4().hex[:8]}"

        # Teoria de Filas (Aether): Menor valor numérico = Maior prioridade no heap
        item = PrioritizedRequest(
            effective_priority=float(base_priority),
            created_at=now,
            request_id=req_id,
            payload=payload,
            future=fut,
            is_stream=stream
        )
        await self.priority_queue.put(item)
        return await fut

    async def _drain_loop(self):
        while self._running:
            item: PrioritizedRequest = await self.priority_queue.get()
            model = item.payload.get("model", "*")
            node = self.select_healthy_node(model)

            if not node:
                # Se não há nó disponível, reaplica envelhecimento e recoloca na fila
                wait_time = time.time() - item.created_at
                item.effective_priority = max(0.0, item.effective_priority - (self.aging_factor * wait_time))
                if wait_time > 60.0:
                    item.future.set_exception(RuntimeError("Gateway timeout: no healthy backend nodes available"))
                else:
                    await asyncio.sleep(0.05)
                    await self.priority_queue.put(item)
                self.priority_queue.task_done()
                continue

            # Despacho assíncrono para o nó selecionado
            asyncio.create_task(self._dispatch_to_node(node, item))
            self.priority_queue.task_done()

    async def _dispatch_to_node(self, node: BackendNode, item: PrioritizedRequest):
        try:
            if item.is_stream:
                gen = node.execute_stream(item.payload)
                item.future.set_result(gen)
            else:
                res = await node.execute(item.payload)
                item.future.set_result(res)
        except Exception as e:
            if not item.future.done():
                item.future.set_exception(e)

    async def start(self):
        self._running = True
        self._worker_task = asyncio.create_task(self._drain_loop())
        logger.info("Unified Inference Gateway iniciado com sucesso.")

    async def stop(self):
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Unified Inference Gateway encerrado.")


class SpecterHttpServer:
    """Zero-dependency HTTP/1.1 Server for OpenAI-compatible REST + SSE endpoints."""
    def __init__(self, gateway: UnifiedInferenceGateway, host: str = "127.0.0.1", port: int = 8080):
        self.gateway = gateway
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        logger.info("Specter HTTP Gateway ouvindo em http://%s:%s", self.host, self.port)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Specter HTTP Gateway encerrado.")

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            req_line = await reader.readline()
            if not req_line:
                writer.close()
                return

            line_str = req_line.decode("utf-8", errors="replace").strip()
            parts = line_str.split()
            if len(parts) < 2:
                writer.close()
                return

            method, path = parts[0].upper(), parts[1]

            # Read headers
            headers = {}
            while True:
                header_line = await reader.readline()
                if not header_line or header_line in (b"\r\n", b"\n"):
                    break
                h_str = header_line.decode("utf-8", errors="replace").strip()
                if ":" in h_str:
                    k, v = h_str.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            # Handle CORS preflight
            if method == "OPTIONS":
                resp = (
                    "HTTP/1.1 204 No Content\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                    "Access-Control-Allow-Headers: Content-Type, Authorization, x-specter-priority\r\n"
                    "Content-Length: 0\r\n\r\n"
                )
                writer.write(resp.encode("utf-8"))
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Read body if Content-Length given
            content_length = int(headers.get("content-length", 0))
            body_bytes = b""
            if content_length > 0:
                body_bytes = await reader.readexactly(content_length)

            # Route: GET /health or /v1/health
            if method == "GET" and path in ("/health", "/v1/health"):
                health_data = {
                    "status": "healthy",
                    "version": "1.1.0",
                    "nodes_count": len(self.gateway.nodes),
                    "queue_size": self.gateway.priority_queue.qsize(),
                    "nodes": [
                        {
                            "node_id": n.node_id,
                            "state": n.circuit.state.value,
                            "models": n.supported_models,
                            "total_requests": n.total_requests
                        }
                        for n in self.gateway.nodes.values()
                    ]
                }
                body = json.dumps(health_data, indent=2).encode("utf-8")
                resp = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json; charset=utf-8\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode("utf-8") + body
                writer.write(resp)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Route: GET /v1/models
            if method == "GET" and path == "/v1/models":
                models = []
                for n in self.gateway.nodes.values():
                    for m in n.supported_models:
                        if m != "*" and m not in [x["id"] for x in models]:
                            models.append({
                                "id": m,
                                "object": "model",
                                "created": 1725600000,
                                "owned_by": n.node_id
                            })
                if not models:
                    models.append({"id": "specter-sovereign-core", "object": "model", "created": 1725600000, "owned_by": "specter"})
                models_data = {"object": "list", "data": models}
                body = json.dumps(models_data, indent=2).encode("utf-8")
                resp = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json; charset=utf-8\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode("utf-8") + body
                writer.write(resp)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            # Route: POST /v1/chat/completions
            if method == "POST" and path == "/v1/chat/completions":
                try:
                    payload = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    err_body = json.dumps({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}).encode("utf-8")
                    resp = (
                        "HTTP/1.1 400 Bad Request\r\n"
                        "Content-Type: application/json; charset=utf-8\r\n"
                        "Access-Control-Allow-Origin: *\r\n"
                        f"Content-Length: {len(err_body)}\r\n\r\n"
                    ).encode("utf-8") + err_body
                    writer.write(resp)
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return

                stream = payload.get("stream", False)
                base_priority = int(headers.get("x-specter-priority", 10))

                try:
                    result = await self.gateway.submit_request(payload, base_priority=base_priority, stream=stream)

                    if stream:
                        header_resp = (
                            "HTTP/1.1 200 OK\r\n"
                            "Content-Type: text/event-stream; charset=utf-8\r\n"
                            "Cache-Control: no-cache\r\n"
                            "Connection: keep-alive\r\n"
                            "Access-Control-Allow-Origin: *\r\n\r\n"
                        ).encode("utf-8")
                        writer.write(header_resp)
                        await writer.drain()

                        async for chunk in result:
                            writer.write(chunk.encode("utf-8"))
                            await writer.drain()
                    else:
                        body = json.dumps(result).encode("utf-8")
                        resp = (
                            "HTTP/1.1 200 OK\r\n"
                            "Content-Type: application/json; charset=utf-8\r\n"
                            "Access-Control-Allow-Origin: *\r\n"
                            f"Content-Length: {len(body)}\r\n\r\n"
                        ).encode("utf-8") + body
                        writer.write(resp)
                        await writer.drain()
                except Exception as e:
                    err_body = json.dumps({"error": {"message": str(e), "type": "gateway_error"}}).encode("utf-8")
                    resp = (
                        "HTTP/1.1 502 Bad Gateway\r\n"
                        "Content-Type: application/json; charset=utf-8\r\n"
                        "Access-Control-Allow-Origin: *\r\n"
                        f"Content-Length: {len(err_body)}\r\n\r\n"
                    ).encode("utf-8") + err_body
                    writer.write(resp)
                    await writer.drain()

                writer.close()
                await writer.wait_closed()
                return

            # Default 404
            not_found = json.dumps({"error": {"message": f"Route not found: {path}", "type": "invalid_request_error"}}).encode("utf-8")
            resp = (
                "HTTP/1.1 404 Not Found\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(not_found)}\r\n\r\n"
            ).encode("utf-8") + not_found
            writer.write(resp)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            logger.error("Erro manipulando conexao HTTP: %s", e)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


def main_cli():
    import argparse
    parser = argparse.ArgumentParser(description="Specter Unified Inference Gateway Server (OpenAI Compatible)")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--mock", action="store_true", default=True, help="Enable mock fallback node if no remote nodes configured")
    args = parser.parse_args()

    async def run_server():
        gateway = UnifiedInferenceGateway()
        if args.mock:
            mock_node = BackendNode(
                node_id="specter_sovereign_node",
                base_url="http://127.0.0.1:11434",
                max_concurrency=4,
                supported_models=["*"],
                is_mock=True
            )
            gateway.register_node(mock_node)

        await gateway.start()
        http_server = SpecterHttpServer(gateway, host=args.host, port=args.port)
        await http_server.start()

        print(f"\n=======================================================")
        print(f" SPECTER UNIFIED INFERENCE GATEWAY (v1.1.0)")
        print(f" OpenAI Endpoint: http://{args.host}:{args.port}/v1/chat/completions")
        print(f" Health Check:   http://{args.host}:{args.port}/health")
        print(f" Models Catalog: http://{args.host}:{args.port}/v1/models")
        print(f"=======================================================\n")

        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await http_server.stop()
            await gateway.stop()

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("\nShutdown complete.")


if __name__ == "__main__":
    main_cli()
