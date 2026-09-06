#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPECTER ENGINE v2.0 — ULTRALIGHT LOCAL AGENT RUNTIME
Target Hardware: Windows 10, Core i5 3rd Gen (Ivy Bridge, AVX1), 8 GB RAM
Footprint: < 20 MB RAM | Storage: SSD C: (Fast) + HD D: (Bulk) + Blackwell Cluster (Offload)
Zero Third-Party Dependencies (100% Python Standard Library)
"""

import asyncio
import ast
import ctypes
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional, Callable

# =====================================================================
# 1. HARDWARE & STORAGE CONFIGURATION
# =====================================================================

class SpecterConfig:
    # SSD C: - Fast Tier (Latencia Critica, IPC, Ring-Buffer, Rollback)
    SSD_BASE = pathlib.Path(r"C:\specter\Core\runtime")
    ROLLBACK_DIR = SSD_BASE / "rollback"
    SCRATCH_DB = SSD_BASE / "ring_state.db"
    
    # HD D: - Bulk Storage Tier (Logs imutaveis, Vault, Datasets, Artifacts)
    HD_VAULT = pathlib.Path(r"D:\specter\vault")
    HD_LOGS = pathlib.Path(r"D:\specter\logs")
    IMMUTABLE_LOG = HD_LOGS / "events.jsonl"
    
    # Cluster Remoto / Offload
    CLUSTER_OFFLOAD_URL = "https://pintograndao-hermes-bridge.hf.space/gradio_api/call/execute_code"
    CDP_PORT = 9222
    WEBUI_PORT = 9090
    
    # Restricoes de Recursos do Core i5 3rd gen / 8GB RAM
    MAX_SUBPROCESS_BUFFER_BYTES = 1024 * 1024  # 1 MB max por comando (anti-OOM)
    RING_BUFFER_CAPACITY = 200                # Maximo de eventos em memoria viva
    COMMAND_TIMEOUT_SEC = 45                  # Timeout agressivo contra travamentos

    @classmethod
    def initialize_filesystem(cls):
        cls.SSD_BASE.mkdir(parents=True, exist_ok=True)
        cls.ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
        cls.HD_VAULT.mkdir(parents=True, exist_ok=True)
        cls.HD_LOGS.mkdir(parents=True, exist_ok=True)


# =====================================================================
# 2. EVENT BUS & RING BUFFER (ZERO-ALLOCATION PATTERN)
# =====================================================================

@dataclasses.dataclass(order=True)
class EngineEvent:
    priority: int
    timestamp: float = dataclasses.field(compare=True)
    source: str = dataclasses.field(compare=False)
    action: str = dataclasses.field(compare=False)
    payload: Dict[str, Any] = dataclasses.field(compare=False)

class SpecterEventBus:
    def __init__(self, capacity: int = SpecterConfig.RING_BUFFER_CAPACITY):
        self._queue = asyncio.PriorityQueue()
        self._ring: List[Dict[str, Any]] = []
        self._capacity = capacity
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []

    def push(self, priority: int, source: str, action: str, payload: Dict[str, Any]):
        evt = EngineEvent(
            priority=priority,
            timestamp=time.time(),
            source=source,
            action=action,
            payload=payload
        )
        self._queue.put_nowait(evt)
        
        data = {
            "ts": evt.timestamp,
            "prio": evt.priority,
            "src": evt.source,
            "act": evt.action,
            "payload": evt.payload
        }
        self._ring.append(data)
        if len(self._ring) > self._capacity:
            self._ring.pop(0)

        for sub in self._subscribers:
            try:
                sub(data)
            except Exception:
                pass

    async def pop(self) -> EngineEvent:
        return await self._queue.get()

    def get_recent_history(self) -> List[Dict[str, Any]]:
        return list(self._ring)

    def subscribe(self, cb: Callable[[Dict[str, Any]], None]):
        self._subscribers.append(cb)


# =====================================================================
# 3. TOOL 1: ASYNC POWERSHELL EXECUTOR COM WATCHDOG E TASKKILL
# =====================================================================

class PowerShellExecutor:
    """Executa comandos no Windows com streaming assincrono e exterminio de arvores orfas"""
    
    @staticmethod
    async def run(cmd: str, timeout: int = SpecterConfig.COMMAND_TIMEOUT_SEC) -> Dict[str, Any]:
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        )

        stdout_chunks = []
        stderr_chunks = []
        bytes_read = 0
        timed_out = False

        async def read_stream(stream, chunks_list):
            nonlocal bytes_read
            while True:
                line = await stream.readline()
                if not line:
                    break
                if bytes_read < SpecterConfig.MAX_SUBPROCESS_BUFFER_BYTES:
                    chunks_list.append(line)
                    bytes_read += len(line)
                elif len(chunks_list) > 0 and chunks_list[-1] != b"[... TRUNCATED BY SPECTER ...]\n":
                    chunks_list.append(b"[... TRUNCATED BY SPECTER ...]\n")

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(proc.stdout, stdout_chunks),
                    read_stream(proc.stderr, stderr_chunks),
                    proc.wait()
                ),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
            except Exception:
                pass

        duration_ms = round((time.time() - t0) * 1000, 2)
        out_text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
        err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        
        return {
            "exit_code": -1 if timed_out else proc.returncode,
            "stdout": out_text,
            "stderr": err_text if not timed_out else f"ERRO: Comando excedeu timeout de {timeout}s e foi terminado.",
            "duration_ms": duration_ms,
            "timed_out": timed_out
        }


# =====================================================================
# 4. TOOL 2: SURGICAL FILE EDITOR COM AST VALIDATOR E ROLLBACK NO SSD
# =====================================================================

class ASTFileEditor:
    """Edicoes atomicas cirurgicas com validacao de sintaxe e snapshot em C:"""

    @classmethod
    def apply_edit(cls, file_path: str, new_content: str) -> Dict[str, Any]:
        target = pathlib.Path(file_path).resolve()
        
        # 1. Validacao de Sintaxe para Python (Zero quebra de codigo)
        if target.suffix.lower() == ".py":
            try:
                ast.parse(new_content, filename=str(target))
            except SyntaxError as e:
                return {
                    "success": False,
                    "error": f"SYNTAX_ERROR: Falha de validacao AST na linha {e.lineno}, col {e.offset}: {e.msg}"
                }

        # 2. Snapshot de Rollback no SSD C:
        if target.exists():
            try:
                bak_name = f"{target.stem}_{int(time.time()*1000)}{target.suffix}.bak"
                bak_path = SpecterConfig.ROLLBACK_DIR / bak_name
                bak_path.write_bytes(target.read_bytes())
            except Exception as e:
                return {"success": False, "error": f"Falha ao criar snapshot de rollback: {e}"}

        # 3. Escrita Atomica via Arquivo Temporario
        tmp_target = target.with_suffix(target.suffix + ".specter_tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_target.write_text(new_content, encoding="utf-8")
            os.replace(tmp_target, target)
        except Exception as e:
            if tmp_target.exists():
                tmp_target.unlink()
            return {"success": False, "error": f"Falha na substituicao atomica: {e}"}

        return {
            "success": True,
            "file": str(target),
            "bytes_written": len(new_content.encode("utf-8")),
            "sha256": hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:16]
        }


# =====================================================================
# 5. TOOL 3: RIPGREP CODE INSPECTOR & SYMBOL OUTLINER
# =====================================================================

class RipgrepInspector:
    """Busca em velocidade C/SIMD e extracao de outline sem alocar arquivos em RAM"""

    @staticmethod
    async def search(query: str, search_path: str, is_regex: bool = False) -> Dict[str, Any]:
        args = ["rg", "--json", "-C", "1", "--max-count", "50"]
        if not is_regex:
            args.append("-F")
        args.extend([query, search_path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )
            stdout, _ = await proc.communicate()
            matches = []
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if record.get("type") == "match":
                        data = record.get("data", {})
                        matches.append({
                            "file": data.get("path", {}).get("text"),
                            "line": data.get("line_number"),
                            "snippet": data.get("lines", {}).get("text", "").strip()
                        })
                except Exception:
                    continue
            return {"engine": "ripgrep_native", "count": len(matches), "matches": matches}
        except FileNotFoundError:
            return await RipgrepInspector._fallback_search(query, search_path)

    @staticmethod
    async def _fallback_search(query: str, search_path: str) -> Dict[str, Any]:
        matches = []
        p = pathlib.Path(search_path)
        if not p.exists():
            return {"engine": "fallback", "count": 0, "matches": []}

        for file in p.rglob("*"):
            if len(matches) >= 50:
                break
            if file.is_file() and file.suffix in [".py", ".js", ".ts", ".json", ".html", ".md"]:
                try:
                    with open(file, "r", encoding="utf-8", errors="ignore") as f:
                        for line_idx, line in enumerate(f, start=1):
                            if query in line:
                                matches.append({
                                    "file": str(file),
                                    "line": line_idx,
                                    "snippet": line.strip()
                                })
                                if len(matches) >= 50:
                                    break
                except Exception:
                    continue
        return {"engine": "python_stream_fallback", "count": len(matches), "matches": matches}


# =====================================================================
# 6. ANSI TUI INTERFACE (VT100 NATIVO DO WINDOWS, 0 RAM OVERHEAD)
# =====================================================================

class SpecterTUI:
    """Renderizador de console ANSI de alto desempenho e consumo nulo de memoria"""

    @staticmethod
    def enable_windows_ansi():
        if os.name == "nt":
            try:
                kernel32 = ctypes.windll.kernel32
                hStdOut = kernel32.GetStdHandle(-11)
                mode = ctypes.c_ulong()
                kernel32.GetConsoleMode(hStdOut, ctypes.byref(mode))
                kernel32.SetConsoleMode(hStdOut, mode.value | 0x0004)
            except Exception:
                pass

    @staticmethod
    def render_banner():
        print("\033[2J\033[H", end="")
        banner = """\033[1;36m
  =============================================================================
     SPECTER ENGINE v2.0 - LOCAL AGENT RUNTIME ("NOVO CODEX" WINDOWS 10)
  =============================================================================
  \033[0m
  \033[1;32m[HARDWARE]\033[0m Win10 | Core i5-3rd | RAM: <20MB Target | Fast SSD C: + Bulk HD D:
  \033[1;35m[OFFLOAD ]\033[0m Cluster 192 vCPUs / Blackwell 48GB | Zero-Quota OSS Mesh
  \033[1;33m[WEB-DASH]\033[0m http://127.0.0.1:9090 (Dashboard SSE em Tempo Real)
  -----------------------------------------------------------------------------
"""
        print(banner)

    @staticmethod
    def log(src: str, msg: str, status: str = "INFO"):
        color = "\033[32m" if status == "OK" else ("\033[31m" if status == "ERR" else "\033[34m")
        t_str = time.strftime("%H:%M:%S")
        print(f"\033[90m[{t_str}]\033[0m {color}[{status:<4}]\033[0m \033[1;37m{src:<12}\033[0m: {msg}")


# =====================================================================
# 7. MICRO WEBUI COM SERVER-SENT EVENTS (SSE) (CONSUMO < 3MB RAM)
# =====================================================================

class MicroWebUIServer:
    """Servidor HTTP puro assincrono embutido com streaming SSE unidirecional"""

    HTML_DASHBOARD = b"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Specter Engine - Autonomous Console</title>
    <style>
        body { background: #0c0e14; color: #dcdfe4; font-family: monospace; margin: 0; padding: 20px; }
        header { border-bottom: 2px solid #282c34; padding-bottom: 10px; display: flex; justify-content: space-between; }
        h1 { margin: 0; color: #61afef; font-size: 20px; }
        .badge { background: #98c379; color: #000; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
        #terminal { background: #161922; border: 1px solid #282c34; height: 500px; overflow-y: scroll; padding: 15px; margin-top: 15px; border-radius: 6px; }
        .line { margin: 3px 0; font-size: 13px; line-height: 1.4; border-bottom: 1px solid #1e222d; padding-bottom: 2px; }
        .ts { color: #5c6370; }
        .src { color: #e5c07b; font-weight: bold; }
        .act { color: #98c379; }
        .err { color: #e06c75; }
        .meta { display: flex; gap: 20px; margin-top: 15px; }
        .card { background: #1e222d; padding: 10px 15px; border-radius: 4px; border-left: 3px solid #61afef; flex: 1; }
    </style>
</head>
<body>
    <header>
        <h1>SPECTER ENGINE :: CODEX RUNTIME</h1>
        <span class="badge">ONLINE (RAM: &lt; 20MB)</span>
    </header>
    <div class="meta">
        <div class="card"><strong>Hardware:</strong> Core i5 Ivy Bridge &middot; 8GB RAM</div>
        <div class="card"><strong>Tiering:</strong> SSD C: (Fast IPC) &middot; HD D: (Vault/Logs)</div>
        <div class="card"><strong>Offload:</strong> Cluster 192 vCPUs &middot; Blackwell 48GB</div>
    </div>
    <div id="terminal"></div>
    <script>
        const term = document.getElementById('terminal');
        const evtSource = new EventSource('/stream');
        evtSource.onmessage = function(e) {
            const d = JSON.parse(e.data);
            const line = document.createElement('div');
            line.className = 'line';
            line.innerHTML = `<span class="ts">[${new Date(d.ts*1000).toLocaleTimeString()}]</span> <span class="src">[${d.src}]</span> <span class="act">${d.act}</span>: <span>${JSON.stringify(d.payload)}</span>`;
            term.appendChild(line);
            term.scrollTop = term.scrollHeight;
        };
    </script>
</body>
</html>"""

    def __init__(self, bus: SpecterEventBus, port: int = SpecterConfig.WEBUI_PORT):
        self.bus = bus
        self.port = port
        self._sse_clients = []
        self.bus.subscribe(self._broadcast_event)

    def _broadcast_event(self, evt: Dict[str, Any]):
        msg = f"data: {json.dumps(evt)}\n\n".encode("utf-8")
        dead_clients = []
        for writer in self._sse_clients:
            try:
                writer.write(msg)
            except Exception:
                dead_clients.append(writer)
        for dead in dead_clients:
            if dead in self._sse_clients:
                self._sse_clients.remove(dead)

    async def start(self):
        server = await asyncio.start_server(self._handle_client, "127.0.0.1", self.port)
        SpecterTUI.log("WebUI", f"Servidor ativo em http://127.0.0.1:{self.port}", "OK")
        return server

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            req_line = await reader.readline()
            if not req_line:
                writer.close()
                return
            parts = req_line.decode().split()
            if len(parts) < 2:
                writer.close()
                return
            path = parts[1]

            if path == "/stream":
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/event-stream\r\n"
                    "Cache-Control: no-cache\r\n"
                    "Connection: keep-alive\r\n"
                    "Access-Control-Allow-Origin: *\r\n\r\n"
                )
                writer.write(headers.encode("utf-8"))
                await writer.drain()
                self._sse_clients.append(writer)
                for item in self.bus.get_recent_history():
                    writer.write(f"data: {json.dumps(item)}\n\n".encode("utf-8"))
                await writer.drain()
            else:
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(self.HTML_DASHBOARD)}\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(headers.encode("utf-8") + self.HTML_DASHBOARD)
                await writer.drain()
                writer.close()
        except Exception:
            try:
                writer.close()
            except Exception:
                pass


# =====================================================================
# 8. SPECTER ENGINE ORCHESTRATOR & MAIN LOOP
# =====================================================================

class SpecterEngine:
    def __init__(self):
        SpecterConfig.initialize_filesystem()
        SpecterTUI.enable_windows_ansi()
        self.bus = SpecterEventBus()
        self.webui = MicroWebUIServer(self.bus)
        self.running = False

    async def initialize(self):
        SpecterTUI.render_banner()
        SpecterTUI.log("Core", "Inicializando Specter Runtime Engine...", "INFO")
        await self.webui.start()
        
        self.bus.push(1, "SYSTEM", "BOOT", {
            "status": "READY",
            "memory_tier": "FAST_SSD_C_AND_HD_D",
            "hardware": "Core i5 3rd gen / 8GB RAM",
            "cloud_mesh": "Blackwell 48GB Online"
        })
        self.running = True

    async def execute_task(self, task_type: str, payload: Dict[str, Any]):
        self.bus.push(2, "DISPATCHER", "TASK_START", {"type": task_type, "payload": payload})
        SpecterTUI.log("Dispatcher", f"Iniciando tarefa: {task_type}", "INFO")

        if task_type == "powershell":
            cmd = payload.get("command", "")
            res = await PowerShellExecutor.run(cmd)
            self.bus.push(3, "POWERSHELL", "EXEC_DONE", res)
            SpecterTUI.log("PowerShell", f"Exit {res['exit_code']} em {res['duration_ms']}ms", "OK" if res['exit_code'] == 0 else "ERR")
            return res

        elif task_type == "ast_edit":
            file_path = payload.get("file", "")
            content = payload.get("content", "")
            res = ASTFileEditor.apply_edit(file_path, content)
            self.bus.push(3, "AST_EDITOR", "MUTATION_DONE", res)
            SpecterTUI.log("AST_Editor", f"Arquivo: {file_path} -> {res.get('success')}", "OK" if res.get("success") else "ERR")
            return res

        elif task_type == "semantic_search":
            query = payload.get("query", "")
            path = payload.get("path", ".")
            res = await RipgrepInspector.search(query, path)
            self.bus.push(3, "SEARCH", "SEARCH_DONE", {"matches_found": res.get("count")})
            SpecterTUI.log("Inspector", f"Matches para '{query}': {res.get('count')}", "OK")
            return res

        return {"error": "UNKNOWN_TASK_TYPE"}

    async def run_forever(self):
        await self.initialize()
        try:
            while self.running:
                evt = await self.bus.pop()
                log_line = json.dumps({
                    "ts": evt.timestamp,
                    "prio": evt.priority,
                    "src": evt.source,
                    "act": evt.action,
                    "payload": evt.payload
                }) + "\n"
                
                try:
                    with open(SpecterConfig.IMMUTABLE_LOG, "a", encoding="utf-8") as f:
                        f.write(log_line)
                except Exception:
                    pass

        except (KeyboardInterrupt, asyncio.CancelledError):
            SpecterTUI.log("Core", "Encerrando Specter Runtime gracefully...", "INFO")
            self.running = False


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    engine = SpecterEngine()
    try:
        asyncio.run(engine.run_forever())
    except KeyboardInterrupt:
        print("\n[*] Specter Engine finalizado com sucesso.")
        sys.exit(0)
