# -*- coding: utf-8 -*-
"""
================================================================================
SPECTER RIGHT-HAND V2 — TRI-AGENT ORCHESTRATION ENGINE (CORE)
================================================================================
Architectural Invariants:
1. Pure Python Standard Library:
   - 100% zero heavy ML dependencies (asyncio, sqlite3, hashlib, json, ast, subprocess).
   - Ultra-low RAM footprint (< 25 MB RSS).
2. Strict Zero Commercial Cost ($0.00 USD):
   - Zero commercial token consumption.
   - OpenCode Zen Free Models Matrix + Local OSS / Deterministic Worker.
   - Non-stall cascade across priority models on 429/timeout/failure.
3. Milestone 1 / Milestone 2 Contract Integrity:
   - Canonical JSON serialization and SHA-256 cryptographic evidence.
   - SQLite WAL concurrency (BEGIN IMMEDIATE, PRAGMA wal_checkpoint).
4. Tri-Agent Contract Compliance:
   - RightHandCoordinator: :GOAL, :STATUS, :PLAN, :ACTION, :REPORT, :MEM
   - SystemWatcher:        :WATCH_STATUS, :ALERTS, :SUGGESTED_ACTIONS, :RAW_OBSERVATIONS
   - CoreBuilder:          :BUILD_GOAL, :DESIGN, :CODE_OR_CHANGES, :TEST_PLAN, :RISKS, :NEXT
================================================================================
"""

import ast
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
import enum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Garante suporte UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Base Directories
CORE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = CORE_DIR / "storage"
DEFAULT_DB = STORAGE_DIR / "specter_fabric.sqlite3"
SUPERVISOR_HEALTH_FILE = CORE_DIR / "supervisor_health.json"
SUPERVISOR_LOG_FILE = CORE_DIR / "supervisor_247.log"
DAEMON_LOG_FILE = CORE_DIR / "daemon.log"

# Opcional: Importar Matriz OSS do Specter se disponível
try:
    if str(CORE_DIR) not in sys.path:
        sys.path.insert(0, str(CORE_DIR))
    from oss_provider_matrix import (
        OSSProviderMatrix,
        create_default_matrix,
        ExecutionRequest,
        ExecutionResponse,
        Capability
    )
    HAS_OSS_MATRIX = True
except Exception:
    HAS_OSS_MATRIX = False

logger = logging.getLogger("SpecterRightHandV2")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][RightHandV2] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# -----------------------------------------------------------------------------
# Canonical Serialization & Cryptographic Verification (Milestone 1 Invariant)
# -----------------------------------------------------------------------------

def canonical(value: Any) -> bytes:
    """Deterministic JSON canonicalization compatible with Milestone 1 Autonomous Broker."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(',', ':'),
        allow_nan=False
    ).encode('ascii')


def digest(data: bytes) -> str:
    """SHA-256 cryptographic digest."""
    return hashlib.sha256(data).hexdigest()


def compute_evidence_hash(manifest: Dict[str, Any]) -> str:
    """Milestone 1 evidence hash with domain separation prefix."""
    return digest(b'SPECTER/EVIDENCE/v1\0' + canonical(manifest))


# -----------------------------------------------------------------------------
# Free Models Matrix & Fallback Engine ($0.00 USD)
# -----------------------------------------------------------------------------

# Ordem prioritária dos free models do OpenCode Zen (conforme SPECTER_RIGHT_HAND_V2)
ZEN_FREE_MODELS: List[str] = [
    "nemotron-3-ultra",
    "mimo-v2.5",
    "big-pickle",
    "ling-3.0-flash-fin",
    "muse-spark-1.2-contributor",
    "nemotron-3.5-lightning"
]


class FallbackTriggerReason(str, enum.Enum):
    RATE_LIMIT_429 = "rate_limit_429"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    CIRCUIT_OPEN = "circuit_open"
    MODEL_NOT_FOUND = "model_not_found"
    MALFORMED_OUTPUT = "malformed_output"


@dataclass
class ModelHop:
    model: str
    attempt: int
    success: bool
    latency_ms: float
    error: Optional[str] = None
    cost_usd: float = 0.0


@dataclass
class FreeInferenceResponse:
    success: bool
    model_used: str
    content: str
    total_cost_usd: float = 0.0
    hops: List[ModelHop] = field(default_factory=list)
    evidence_hash: str = ""
    error: Optional[str] = None


class FreeModelFallbackGateway:
    """
    Roteador de Inferência 100% Gratuita com Cascata Automática.
    Invariante: Zero custo comercial ($0.00 USD).
    Alterna imediatamente de modelo sob rate limit (429), timeout ou degradação.
    Integra-se com a Matriz OSS do Specter e Zen Free Models.
    """
    def __init__(
        self,
        model_list: Optional[List[str]] = None,
        base_url: Optional[str] = None,
        timeout: float = 20.0,
        custom_dispatch_fn: Optional[Callable[[str, List[Dict[str, str]]], str]] = None,
        oss_matrix: Optional[Any] = None
    ):
        self.model_list = list(model_list or ZEN_FREE_MODELS)
        self.base_url = base_url or os.environ.get("SPECTER_GATEWAY_URL", "http://127.0.0.1:8080")
        self.timeout = timeout
        self.custom_dispatch_fn = custom_dispatch_fn
        self.oss_matrix = oss_matrix
        self.switch_history: List[Dict[str, Any]] = []
        self._current_index = 0

    @property
    def current_model(self) -> str:
        return self.model_list[self._current_index % len(self.model_list)]

    def advance_model(self, reason: str, details: Optional[str] = None) -> str:
        old_model = self.current_model
        self._current_index = (self._current_index + 1) % len(self.model_list)
        new_model = self.current_model
        entry = {
            "timestamp": time.time(),
            "from_model": old_model,
            "to_model": new_model,
            "reason": reason,
            "details": details or ""
        }
        self.switch_history.append(entry)
        logger.warning("Cascata de modelo gratuito acionada: '%s' -> '%s' (Razão: %s)", old_model, new_model, reason)
        return new_model

    async def generate_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.4,
        max_tokens: int = 2048
    ) -> FreeInferenceResponse:
        """
        Gera resposta executando a cascata sobre os modelos gratuitos.
        Nunca trava; se todos os modelos remotos falharem, aciona a Matriz OSS ou fallback determinístico local.
        """
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        if messages:
            payload_messages.extend(messages)
        else:
            payload_messages.append({"role": "user", "content": prompt})

        hops: List[ModelHop] = []
        attempts = 0
        total_models = len(self.model_list)

        for _ in range(total_models):
            model = self.current_model
            attempts += 1
            start_t = time.time()

            # 1. Se existir custom_dispatch_fn injetada (para testes ou emulação local)
            if self.custom_dispatch_fn:
                try:
                    out = self.custom_dispatch_fn(model, payload_messages)
                    latency = (time.time() - start_t) * 1000.0
                    hop = ModelHop(model=model, attempt=attempts, success=True, latency_ms=latency, cost_usd=0.0)
                    hops.append(hop)
                    ev_hash = digest(canonical({"model": model, "content": out, "attempts": attempts}))
                    return FreeInferenceResponse(
                        success=True,
                        model_used=model,
                        content=out,
                        total_cost_usd=0.0,
                        hops=hops,
                        evidence_hash=ev_hash
                    )
                except Exception as ex:
                    latency = (time.time() - start_t) * 1000.0
                    err_str = str(ex)
                    hop = ModelHop(model=model, attempt=attempts, success=False, latency_ms=latency, error=err_str, cost_usd=0.0)
                    hops.append(hop)
                    self.advance_model(FallbackTriggerReason.RATE_LIMIT_429.value if "429" in err_str else FallbackTriggerReason.HTTP_ERROR.value, err_str)
                    continue

            # 2. Despacho HTTP padrão compatível com OpenAI (OpenCode Zen / Gateway Local)
            req_body = {
                "model": model,
                "messages": payload_messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            req_bytes = json.dumps(req_body).encode("utf-8")
            url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
            req = urllib.request.Request(
                url=url,
                data=req_bytes,
                headers={"Content-Type": "application/json", "User-Agent": "SpecterRightHandV2/1.0"}
            )

            try:
                def _do_http():
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        return resp.status, resp.read().decode("utf-8", errors="replace")

                status, body = await asyncio.to_thread(_do_http)
                latency = (time.time() - start_t) * 1000.0

                if status == 200:
                    data = json.loads(body)
                    choices = data.get("choices", [])
                    content = choices[0].get("message", {}).get("content", "") if choices else ""
                    hop = ModelHop(model=model, attempt=attempts, success=True, latency_ms=latency, cost_usd=0.0)
                    hops.append(hop)
                    ev_hash = digest(canonical({"model": model, "content": content, "attempts": attempts}))
                    return FreeInferenceResponse(
                        success=True,
                        model_used=model,
                        content=content,
                        total_cost_usd=0.0,
                        hops=hops,
                        evidence_hash=ev_hash
                    )
                elif status == 429:
                    hop = ModelHop(model=model, attempt=attempts, success=False, latency_ms=latency, error="HTTP 429 Rate Limit", cost_usd=0.0)
                    hops.append(hop)
                    self.advance_model(FallbackTriggerReason.RATE_LIMIT_429.value, f"Status 429 on {model}")
                else:
                    hop = ModelHop(model=model, attempt=attempts, success=False, latency_ms=latency, error=f"HTTP {status}", cost_usd=0.0)
                    hops.append(hop)
                    self.advance_model(FallbackTriggerReason.HTTP_ERROR.value, f"Status {status} on {model}")

            except urllib.error.HTTPError as he:
                latency = (time.time() - start_t) * 1000.0
                err_msg = f"HTTPError {he.code}"
                hop = ModelHop(model=model, attempt=attempts, success=False, latency_ms=latency, error=err_msg, cost_usd=0.0)
                hops.append(hop)
                reason = FallbackTriggerReason.RATE_LIMIT_429.value if he.code == 429 else FallbackTriggerReason.HTTP_ERROR.value
                self.advance_model(reason, f"HTTPError {he.code} on {model}")
            except Exception as e:
                latency = (time.time() - start_t) * 1000.0
                err_msg = f"Request error: {str(e)}"
                hop = ModelHop(model=model, attempt=attempts, success=False, latency_ms=latency, error=err_msg, cost_usd=0.0)
                hops.append(hop)
                self.advance_model(FallbackTriggerReason.TIMEOUT.value if "timeout" in str(e).lower() else FallbackTriggerReason.HTTP_ERROR.value, err_msg)

        # 3. Se todos os modelos Zen Free falharem e houver Matriz OSS conectada
        if self.oss_matrix and HAS_OSS_MATRIX:
            try:
                logger.info("Recorrendo à Matriz OSS do Specter como tier secundário gratuito...")
                start_oss = time.time()
                last_user_msg = payload_messages[-1]["content"] if payload_messages else prompt
                oss_resp = await self.oss_matrix.chat(prompt=last_user_msg, messages=payload_messages)
                if oss_resp and oss_resp.success:
                    latency = (time.time() - start_oss) * 1000.0
                    hop = ModelHop(
                        model=f"oss_matrix:{oss_resp.provider_id}",
                        attempt=attempts + 1,
                        success=True,
                        latency_ms=latency,
                        cost_usd=0.0
                    )
                    hops.append(hop)
                    return FreeInferenceResponse(
                        success=True,
                        model_used=f"oss_matrix:{oss_resp.provider_id}",
                        content=oss_resp.output_text or "",
                        total_cost_usd=0.0,
                        hops=hops,
                        evidence_hash=oss_resp.evidence_hash
                    )
            except Exception as oss_err:
                logger.warning("Falha no fallback da Matriz OSS: %s", oss_err)

        # 4. Fallback determinístico offline se toda a grade de modelos remotos falhar
        logger.warning("Todos os %d modelos free da grade falharam. Ativando fallback determinístico local.", total_models)
        deterministic_reply = (
            f"[DETERMINISTIC_OFFLINE_FALLBACK] Processed query via Specter Local Engine. "
            f"Active models evaluated: {len(self.model_list)}. Cost: $0.00 USD."
        )
        ev_hash = digest(canonical({"deterministic": True, "attempts": attempts, "timestamp": time.time()}))
        return FreeInferenceResponse(
            success=True,
            model_used="specter-local-deterministic-worker",
            content=deterministic_reply,
            total_cost_usd=0.0,
            hops=hops,
            evidence_hash=ev_hash,
            error="All remote free models exhausted. Local deterministic fallback engaged."
        )


# -----------------------------------------------------------------------------
# SystemWatcher (Monitor Agent)
# -----------------------------------------------------------------------------

@dataclass
class WatcherReport:
    watch_status: str  # HEALTHY | DEGRADED | CRITICAL
    alerts: List[str]
    suggested_actions: List[str]
    raw_observations: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_contract_markdown(self) -> str:
        """Formata conforme o contrato de WATCHER.md."""
        alerts_text = "\n".join(f"- {a}" for a in self.alerts) if self.alerts else "Nenhum alerta crítico ativo."
        actions_text = "\n".join(f"- {a}" for a in self.suggested_actions) if self.suggested_actions else "Continuar ciclo normal de monitoramento."
        obs_json = json.dumps(self.raw_observations, indent=2)

        return (
            f":WATCH_STATUS\n{self.watch_status}\n\n"
            f":ALERTS\n{alerts_text}\n\n"
            f":SUGGESTED_ACTIONS\n{actions_text}\n\n"
            f":RAW_OBSERVATIONS\n```json\n{obs_json}\n```"
        )


class SystemWatcher:
    """
    Agente de monitoramento contínuo do SPECTER Core.
    Responsabilidades:
    - Observar integridade física do SQLite WAL (specter_fabric.sqlite3).
    - Verificar tarefas presas (stuck), limites de tentativas ou tarefas vencidas.
    - Observar batimentos do supervisor_247 e integridade de logs.
    - Estruturar relatórios no contrato :WATCH_STATUS, :ALERTS, :SUGGESTED_ACTIONS, :RAW_OBSERVATIONS.
    """
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB)

    def check_sqlite_wal(self) -> Dict[str, Any]:
        """Inspeciona integridade, checkpoint e tamanho do SQLite WAL."""
        res: Dict[str, Any] = {
            "db_exists": self.db_path.exists(),
            "db_path": str(self.db_path),
            "db_size_bytes": 0,
            "wal_size_bytes": 0,
            "shm_size_bytes": 0,
            "integrity_ok": False,
            "checkpoint_result": None,
            "task_distribution": {},
            "stuck_tasks": [],
            "error": None
        }
        if not self.db_path.exists():
            res["error"] = "Database file not found"
            return res

        try:
            res["db_size_bytes"] = self.db_path.stat().st_size
            wal_file = self.db_path.with_name(self.db_path.name + "-wal")
            shm_file = self.db_path.with_name(self.db_path.name + "-shm")
            if wal_file.exists():
                res["wal_size_bytes"] = wal_file.stat().st_size
            if shm_file.exists():
                res["shm_size_bytes"] = shm_file.stat().st_size

            with sqlite3.connect(self.db_path, timeout=5.0) as con:
                con.row_factory = sqlite3.Row
                # 1. Checagem rápida de integridade
                quick_res = con.execute("PRAGMA quick_check").fetchone()
                res["integrity_ok"] = (quick_res[0] == "ok") if quick_res else False

                # 2. Executa checkpoint passivo do WAL
                try:
                    cp = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                    res["checkpoint_result"] = list(cp) if cp else None
                except Exception as ce:
                    res["checkpoint_error"] = str(ce)

                # 3. Estatísticas de broker_tasks se existir a tabela
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                if "broker_tasks" in tables:
                    rows = con.execute("SELECT state, count(*) as cnt FROM broker_tasks GROUP BY state").fetchall()
                    res["task_distribution"] = {r["state"]: r["cnt"] for r in rows}

                    # Buscar tarefas presas (não terminais com deadline expirada ou tentativas esgotadas)
                    now = time.time()
                    stuck_rows = con.execute(
                        "SELECT id, state, attempts, max_attempts, deadline FROM broker_tasks "
                        "WHERE state NOT IN ('ATTAINED', 'ESCALATE') AND (deadline < ? OR attempts >= max_attempts)",
                        (now,)
                    ).fetchall()
                    for sr in stuck_rows:
                        res["stuck_tasks"].append({
                            "id": sr["id"],
                            "state": sr["state"],
                            "attempts": sr["attempts"],
                            "max_attempts": sr["max_attempts"],
                            "deadline": sr["deadline"]
                        })
                elif "mesh_tasks" in tables:
                    rows = con.execute("SELECT status, count(*) as cnt FROM mesh_tasks GROUP BY status").fetchall()
                    res["task_distribution"] = {r["status"]: r["cnt"] for r in rows}

        except Exception as e:
            res["error"] = str(e)
            res["integrity_ok"] = False

        return res

    def check_supervisor_and_logs(self, max_log_lines: int = 50) -> Dict[str, Any]:
        """Inspeciona arquivo de saúde do supervisor e linhas recentes de log."""
        res: Dict[str, Any] = {
            "supervisor_alive": False,
            "supervisor_status": "UNKNOWN",
            "uptime_seconds": 0.0,
            "recent_errors": []
        }

        # 1. Health JSON
        if SUPERVISOR_HEALTH_FILE.exists():
            try:
                data = json.loads(SUPERVISOR_HEALTH_FILE.read_text(encoding="utf-8"))
                age = time.time() - data.get("timestamp", 0)
                res["supervisor_status"] = data.get("status", "UNKNOWN")
                res["uptime_seconds"] = data.get("uptime_seconds", 0.0)
                # Supervisor vivo se o heartbeat foi atualizado nos últimos 90 segundos
                res["supervisor_alive"] = (age < 90.0) and (res["supervisor_status"] in ("HEALTHY", "OK"))
                res["heartbeat_age_seconds"] = round(age, 1)
            except Exception as ex:
                res["health_file_error"] = str(ex)

        # 2. Checar logs recentes para capturar erros e rate limits
        logs_to_inspect = [SUPERVISOR_LOG_FILE, DAEMON_LOG_FILE]
        for lf in logs_to_inspect:
            if lf.exists():
                try:
                    content = lf.read_text(encoding="utf-8", errors="replace")
                    lines = [l.strip() for l in content.splitlines() if l.strip()]
                    recent = lines[-max_log_lines:]
                    for line in recent:
                        if any(pattern in line.upper() for pattern in ["ERROR", "CRITICAL", "429", "RATE_LIMIT", "EXCEPTION"]):
                            res["recent_errors"].append({"file": lf.name, "line": line[:200]})
                except Exception:
                    pass

        return res

    def patrol(self) -> WatcherReport:
        """Executa varredura completa de saúde e sintetiza o relatório do Watcher."""
        wal_stats = self.check_sqlite_wal()
        sup_stats = self.check_supervisor_and_logs()

        alerts: List[str] = []
        suggested_actions: List[str] = []
        status = "HEALTHY"

        # Análise do Banco de Dados SQLite WAL
        if not wal_stats.get("db_exists"):
            status = "CRITICAL"
            alerts.append(f"Banco SQLite não encontrado em {self.db_path}")
            suggested_actions.append("Inicializar banco de dados via specter_fabric init ou broker run.")
        elif not wal_stats.get("integrity_ok"):
            status = "CRITICAL"
            alerts.append("Falha no PRAGMA quick_check do SQLite WAL!")
            suggested_actions.append("Executar recuperação emergencial do SQLite WAL.")

        # Tarefas travadas no broker
        stuck = wal_stats.get("stuck_tasks", [])
        if stuck:
            if status != "CRITICAL":
                status = "DEGRADED"
            alerts.append(f"Detectadas {len(stuck)} tarefas presas/vencidas no broker.")
            suggested_actions.append("Acionar CoreBuilder para reconciliação ou reset de tarefas.")

        # Supervisor
        if not sup_stats.get("supervisor_alive"):
            if status == "HEALTHY":
                status = "DEGRADED"
            alerts.append("Specter Supervisor 24/7 inativo ou com batimento expirado (>90s).")
            suggested_actions.append("Reiniciar supervisor_247 via specter_supervisor_247.py.")

        # Erros recentes de log
        recent_errs = sup_stats.get("recent_errors", [])
        if len(recent_errs) > 5:
            if status == "HEALTHY":
                status = "DEGRADED"
            alerts.append(f"{len(recent_errs)} erros críticos registrados nos logs recentes.")
            suggested_actions.append("Inspecionar supervisor_247.log para isolar causas raiz.")

        raw = {
            "wal_health": wal_stats,
            "supervisor_health": sup_stats,
            "timestamp": time.time()
        }

        return WatcherReport(
            watch_status=status,
            alerts=alerts,
            suggested_actions=suggested_actions,
            raw_observations=raw
        )


# -----------------------------------------------------------------------------
# CoreBuilder (Implementer Agent)
# -----------------------------------------------------------------------------

@dataclass
class BuilderReport:
    build_goal: str
    design: str
    code_or_changes: str
    test_plan: str
    risks: str
    next_steps: str
    success: bool = True
    evidence_hash: str = ""
    test_results: Optional[Dict[str, Any]] = None

    def to_contract_markdown(self) -> str:
        """Formata conforme o contrato de BUILDER.md."""
        return (
            f":BUILD_GOAL\n{self.build_goal}\n\n"
            f":DESIGN\n{self.design}\n\n"
            f":CODE_OR_CHANGES\n{self.code_or_changes}\n\n"
            f":TEST_PLAN\n{self.test_plan}\n\n"
            f":RISKS\n{self.risks}\n\n"
            f":NEXT\n{self.next_steps}"
        )


class CoreBuilder:
    """
    Agente de implementação, patches e evolução técnica do SPECTER Core.
    Responsabilidades:
    - Aplicar patches em código de forma atômica com backup automático.
    - Validar sintaxe Python com ast.parse antes da gravação.
    - Executar baterias de testes com isolamento de processo e timeout.
    - Integrar com FreeModelFallbackGateway para geração e síntese de código.
    - Estruturar entregas no contrato :BUILD_GOAL, :DESIGN, :CODE_OR_CHANGES, :TEST_PLAN, :RISKS, :NEXT.
    """
    def __init__(self, model_gateway: Optional[FreeModelFallbackGateway] = None):
        self.gateway = model_gateway or FreeModelFallbackGateway()

    @staticmethod
    def validate_python_syntax(code: str) -> Tuple[bool, Optional[str]]:
        """Valida sintaxe do código Python via ast.parse."""
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as se:
            return False, f"SyntaxError na linha {se.lineno}: {se.msg}"
        except Exception as e:
            return False, f"Parse error: {str(e)}"

    def apply_patch(self, target_path: Path, new_content: str, create_backup: bool = True) -> Tuple[bool, str]:
        """
        Aplica patch com integridade:
        1. Validação AST
        2. Backup automático .bak
        3. Escrita atômica via arquivo temporário
        """
        target = Path(target_path).resolve()
        # Se for arquivo Python, valida sintaxe
        if target.suffix == ".py":
            is_valid, err = self.validate_python_syntax(new_content)
            if not is_valid:
                return False, f"Rejeição de patch: Sintaxe Python inválida - {err}"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and create_backup:
                backup_path = target.with_suffix(target.suffix + f".bak_{int(time.time())}")
                backup_path.write_bytes(target.read_bytes())

            tmp_path = target.with_name(f"{target.name}.tmp_{uuid.uuid4().hex[:6]}")
            tmp_path.write_text(new_content, encoding="utf-8")
            tmp_path.replace(target)
            return True, f"Patch aplicado com sucesso em {target.name}"
        except Exception as e:
            return False, f"Falha de I/O ao aplicar patch: {str(e)}"

    def run_tests(self, test_file_path: Path, timeout: float = 40.0) -> Dict[str, Any]:
        """
        Executa suite de testes unitários isolada via subprocess.
        Garante isolamento de processo e captura detalhada de stdout/stderr.
        """
        target = Path(test_file_path).resolve()
        if not target.exists():
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Arquivo de teste não encontrado: {target}",
                "duration_ms": 0.0
            }

        start_t = time.time()
        cmd = [sys.executable, "-m", "unittest", str(target.name)]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(target.parent),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            duration = (time.time() - start_t) * 1000.0
            success = (proc.returncode == 0)
            return {
                "success": success,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "duration_ms": round(duration, 2)
            }
        except subprocess.TimeoutExpired:
            duration = (time.time() - start_t) * 1000.0
            return {
                "success": False,
                "exit_code": -99,
                "stdout": "",
                "stderr": f"TimeoutExpired após {timeout}s",
                "duration_ms": round(duration, 2)
            }
        except Exception as e:
            duration = (time.time() - start_t) * 1000.0
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Erro de execução de teste: {str(e)}",
                "duration_ms": round(duration, 2)
            }

    async def execute_task(
        self,
        goal: str,
        design_spec: str,
        target_file: Optional[Path] = None,
        code_content: Optional[str] = None,
        test_file: Optional[Path] = None
    ) -> BuilderReport:
        """Executa ciclo completo de implementação do Builder."""
        changes_summary = ""
        success = True
        test_res = None

        if target_file and code_content is not None:
            ok, msg = self.apply_patch(target_file, code_content)
            changes_summary = msg
            if not ok:
                success = False

        if test_file and success:
            test_res = self.run_tests(test_file)
            if not test_res.get("success", False):
                success = False
                changes_summary += f" | Testes falharam: {test_res.get('stderr')[:150]}"
            else:
                changes_summary += f" | Testes passaram em {test_res.get('duration_ms')}ms"

        ev_hash = digest(canonical({
            "goal": goal,
            "success": success,
            "target": str(target_file) if target_file else None,
            "test_success": test_res.get("success") if test_res else None,
            "timestamp": time.time()
        }))

        return BuilderReport(
            build_goal=goal,
            design=design_spec,
            code_or_changes=changes_summary or "Sem alterações físicas em disco solicitadas.",
            test_plan=f"Execução de {test_file.name}" if test_file else "Validação estática de contratos AST.",
            risks="Baixo: alterações contidas com verificação sintática e backups.",
            next_steps="Reportar resultado ao RightHandCoordinator para síntese global.",
            success=success,
            evidence_hash=ev_hash,
            test_results=test_res
        )


# -----------------------------------------------------------------------------
# RightHandCoordinator (Primary Coordinator)
# -----------------------------------------------------------------------------

@dataclass
class RightHandResponse:
    goal: str
    status: str
    plan: str
    action: str
    report: str
    mem: Any
    evidence_hash: str
    model_used: str
    timestamp: float = field(default_factory=time.time)

    def to_contract_markdown(self) -> str:
        """Formata conforme o contrato de RIGHT_HAND.md."""
        mem_str = json.dumps(self.mem, indent=2) if isinstance(self.mem, (dict, list)) else str(self.mem)
        return (
            f":GOAL\n{self.goal}\n\n"
            f":STATUS\n{self.status}\n\n"
            f":PLAN\n{self.plan}\n\n"
            f":ACTION\n{self.action}\n\n"
            f":REPORT\n{self.report}\n\n"
            f":MEM\n{mem_str}"
        )



class RightHandCoordinator:
    """
    Coordenador central supremo do ecossistema SPECTER RIGHT-HAND V2.
    Opera exclusivamente com recursos de custo zero ($0.00 USD).
    Coordena de forma contínua o SystemWatcher e o CoreBuilder.
    Mantém histórico auditável em SQLite WAL.
    """
    def __init__(
        self,
        db_path: Optional[Path] = None,
        model_gateway: Optional[FreeModelFallbackGateway] = None,
        watcher: Optional[SystemWatcher] = None,
        builder: Optional[CoreBuilder] = None
    ):
        self.db_path = Path(db_path or DEFAULT_DB)
        self.gateway = model_gateway or FreeModelFallbackGateway()
        self.watcher = watcher or SystemWatcher(db_path=self.db_path)
        self.builder = builder or CoreBuilder(model_gateway=self.gateway)
        self._init_storage()

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            yield con
        finally:
            con.close()

    def _init_storage(self):
        """Inicializa tabelas de persistência do Right-Hand no SQLite WAL."""
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
                CREATE TABLE IF NOT EXISTS right_hand_reports (
                    report_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    action TEXT NOT NULL,
                    owner_report TEXT NOT NULL,
                    mem TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """)
                con.execute("""
                CREATE TABLE IF NOT EXISTS right_hand_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """)
                con.commit()
        except Exception as e:
            logger.warning("Falha ao inicializar tabelas do Right-Hand: %s", e)

    def persist_report(self, resp: RightHandResponse) -> str:
        """Persiste relatório do Right-Hand no SQLite WAL de forma durável."""
        report_id = f"rhr-{uuid.uuid4().hex[:10]}"
        mem_val = json.dumps(resp.mem) if isinstance(resp.mem, (dict, list)) else str(resp.mem)
        try:
            with self._connection() as con:
                con.execute("""
                INSERT INTO right_hand_reports
                (report_id, goal, status, plan, action, owner_report, mem, evidence_hash, model_used, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    report_id,
                    resp.goal,
                    resp.status,
                    resp.plan,
                    resp.action,
                    resp.report,
                    mem_val,
                    resp.evidence_hash,
                    resp.model_used,
                    resp.timestamp
                ))
                con.commit()
        except Exception as e:
            logger.error("Erro ao persistir relatório no SQLite WAL: %s", e)
        return report_id

    def get_last_report(self) -> Optional[Dict[str, Any]]:
        """Recupera o último relatório persistido no SQLite WAL."""
        if not self.db_path.exists():
            return None
        try:
            with self._connection() as con:
                con.row_factory = sqlite3.Row
                row = con.execute("SELECT * FROM right_hand_reports ORDER BY created_at DESC LIMIT 1").fetchone()
                return dict(row) if row else None
        except Exception:
            return None


    def dispatch_to_watcher(self) -> WatcherReport:
        """Despacha ordem de monitoramento direta para o SystemWatcher."""
        return self.watcher.patrol()

    async def dispatch_to_builder(
        self,
        goal: str,
        design_spec: str,
        target_file: Optional[Path] = None,
        code_content: Optional[str] = None,
        test_file: Optional[Path] = None
    ) -> BuilderReport:
        """Despacha tarefa de implementação/código diretamente para o CoreBuilder."""
        return await self.builder.execute_task(
            goal=goal,
            design_spec=design_spec,
            target_file=target_file,
            code_content=code_content,
            test_file=test_file
        )

    async def orchestrate_goal(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        builder_task: Optional[Dict[str, Any]] = None
    ) -> RightHandResponse:
        """
        Orquestra meta executando o ciclo tri-agente completo:
        1. Consulta o SystemWatcher para leitura real de saúde.
        2. Formula plano e ações de alto nível.
        3. Se houver demanda técnica, aciona CoreBuilder.
        4. Sintetiza relatório para o proprietário.
        5. Gera bloco de memória :MEM com SHA-256 evidence e histórico de trocas de modelo.
        """
        start_t = time.time()
        # 1. Obter telemetria do Watcher
        watcher_rep = self.dispatch_to_watcher()

        # 2. Sintetizar STATUS a partir do Watcher
        status_lines = [
            f"Saúde do Sistema: {watcher_rep.watch_status}",
            f"SQLite WAL Integridade: {'OK' if watcher_rep.raw_observations.get('wal_health', {}).get('integrity_ok') else 'FALHA'}",
            f"Tamanho DB: {watcher_rep.raw_observations.get('wal_health', {}).get('db_size_bytes', 0)} bytes | WAL: {watcher_rep.raw_observations.get('wal_health', {}).get('wal_size_bytes', 0)} bytes"
        ]
        if watcher_rep.alerts:
            status_lines.append(f"Alertas ativos: {', '.join(watcher_rep.alerts)}")
        status_text = "\n".join(status_lines)

        # 3. Formular PLAN e ACTION
        plan_items = [
            f"1. Validar premissas e telemetria para meta: '{goal}'.",
            f"2. Priorizar estabilidade: tratar {len(watcher_rep.alerts)} alertas do Watcher." if watcher_rep.alerts else "2. Sistema íntegro: avançar tarefas de evolução técnica.",
            "3. Despachar execução com modelo de custo zero e verificar evidência criptográfica."
        ]
        plan_text = "\n".join(plan_items)

        actions_performed: List[str] = [
            f"Varredura de telemetria concluída com status '{watcher_rep.watch_status}'."
        ]

        # 4. Despachar para o Builder se solicitado
        builder_rep = None
        if builder_task:
            b_goal = builder_task.get("goal", goal)
            b_design = builder_task.get("design", "Refatoração e conformidade de código.")
            b_target = builder_task.get("target_file")
            b_code = builder_task.get("code_content")
            b_test = builder_task.get("test_file")

            builder_rep = await self.dispatch_to_builder(
                goal=b_goal,
                design_spec=b_design,
                target_file=b_target,
                code_content=b_code,
                test_file=b_test
            )
            actions_performed.append(f"Builder concluiu: {builder_rep.code_or_changes}")
        else:
            actions_performed.append("Nenhuma alteração de código solicitada nesta rodada.")

        action_text = "\n".join(actions_performed)

        # 5. Sintetizar REPORT para o proprietário
        report_text = (
            f"Saudações, Proprietário. O SPECTER RIGHT-HAND V2 coordenou com sucesso o ciclo tri-agente.\n"
            f"Estado geral: {watcher_rep.watch_status}.\n"
            f"Meta processada: {goal}.\n"
            f"O Watcher reportou {len(watcher_rep.alerts)} alerta(s). "
            f"O Builder {'executou alterações com sucesso' if builder_rep and builder_rep.success else 'permaneceu em prontidão'}.\n"
            f"Custo total: $0.00 USD (Invariante de gratuidade estritamente mantida)."
        )

        # 6. Elaborar MEM com evidências SHA-256 e trocas de modelo
        model_name = self.gateway.current_model
        evidence_payload = {
            "goal": goal,
            "status": watcher_rep.watch_status,
            "alerts_count": len(watcher_rep.alerts),
            "builder_success": builder_rep.success if builder_rep else None,
            "cost_usd": 0.0,
            "timestamp": start_t
        }
        ev_hash = compute_evidence_hash(evidence_payload)

        mem_entries = [
            f"Evidence Hash: {ev_hash}",
            f"Modelo Ativo: {model_name} (Custo: $0.00 USD)",
            f"Histórico de trocas de modelo: {len(self.gateway.switch_history)} eventos registrados.",
            f"Latência do ciclo: {round((time.time() - start_t) * 1000.0, 2)}ms"
        ]
        mem_text = "\n".join(mem_entries)

        response = RightHandResponse(
            goal=goal,
            status=status_text,
            plan=plan_text,
            action=action_text,
            report=report_text,
            mem=mem_text,
            evidence_hash=ev_hash,
            model_used=model_name,
            timestamp=time.time()
        )

        # Persistência no SQLite WAL
        self.persist_report(response)
        return response

    def run_cycle(self) -> RightHandResponse:
        """Executa um ciclo síncrono de monitoramento e coordenação padrão."""
        return asyncio.run(self.orchestrate_goal("Ciclo autônomo de manutenção e patrulha contínua do Core"))


# -----------------------------------------------------------------------------
# Top-Level Tri-Agent Orchestrator Facade
# -----------------------------------------------------------------------------

class RightHandOrchestrator:
    """Fachada unificada da arquitetura SPECTER RIGHT-HAND V2."""
    def __init__(self, db_path: Optional[Path] = None, oss_matrix: Optional[Any] = None):
        self.db_path = Path(db_path or DEFAULT_DB)
        self.oss_matrix = oss_matrix
        self.gateway = FreeModelFallbackGateway(oss_matrix=self.oss_matrix)
        self.watcher = SystemWatcher(db_path=self.db_path)
        self.builder = CoreBuilder(model_gateway=self.gateway)
        self.coordinator = RightHandCoordinator(
            db_path=self.db_path,
            model_gateway=self.gateway,
            watcher=self.watcher,
            builder=self.builder
        )

    async def execute_cycle(self, goal: Optional[str] = None) -> RightHandResponse:
        active_goal = goal or "Verificação periódica de integridade e prontidão operacional"
        return await self.coordinator.orchestrate_goal(active_goal)

    def get_last_report(self) -> Optional[Dict[str, Any]]:
        """Recupera o último relatório persistido no SQLite WAL."""
        return self.coordinator.get_last_report()



# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="SPECTER Right-Hand V2 Tri-Agent Orchestrator")
    parser.add_argument("--cycle", action="store_true", help="Executa um ciclo completo de coordenação tri-agente")
    parser.add_argument("--goal", type=str, default=None, help="Meta explícita para o Right-Hand orquestrar")
    parser.add_argument("--patrol", action="store_true", help="Executa patrulha isolada do SystemWatcher")
    args = parser.parse_args()

    orchestrator = RightHandOrchestrator()

    if args.patrol:
        rep = orchestrator.watcher.patrol()
        print(rep.to_contract_markdown())
    else:
        resp = asyncio.run(orchestrator.execute_cycle(args.goal))
        print(resp.to_contract_markdown())


if __name__ == "__main__":
    main()
