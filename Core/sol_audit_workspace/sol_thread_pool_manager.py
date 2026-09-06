# -*- coding: utf-8 -*-
"""
SPECTER SOL MULTI-THREAD POOL MANAGER (CODE-MAX v1.0)
Gerenciador de tarefas particionadas em threads isoladas do ChatGPT.
Evita retenção por rate-limit serializando o pipeline por thread dedicada,
respeitando o tempo de raciocínio profundo de cada sub-tarefa.
"""
import time
import json
import uuid
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional

import sys
CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from sol_bridge_v2.bridge_job_state import BridgeJobManager, JobState

class SolThreadPoolManager:
    def __init__(self, db_path: Optional[Path] = None, max_threads: int = 4, registry_file: Optional[Path] = None):
        self.manager = BridgeJobManager(db_path=db_path)
        self.max_threads = max_threads
        self.thread_registry_file = Path(registry_file) if registry_file else CORE_DIR / "config" / "sol_threads.json"
        self._init_registry()

    def _init_registry(self):
        self.thread_registry_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.thread_registry_file.exists():
            default_threads = [
                {"thread_id": f"thread_sol_{i+1}", "status": "IDLE", "assigned_job": None, "last_active": time.time()}
                for i in range(self.max_threads)
            ]
            self.thread_registry_file.write_text(json.dumps(default_threads, indent=2), encoding="utf-8")

    def get_available_thread(self) -> Optional[Dict[str, Any]]:
        threads = json.loads(self.thread_registry_file.read_text(encoding="utf-8"))
        for t in threads:
            if t.get("status") == "IDLE":
                return t
        return None

    def partition_complex_goal(self, main_goal: str, subtasks: List[Dict[str, Any]]) -> List[str]:
        """Submete sub-tarefas atômicas independentes para a fila do bridge."""
        submitted_ids = []
        for idx, st in enumerate(subtasks):
            key = f"sol_subtask_{hashlib.sha256((main_goal + str(idx)).encode()).hexdigest()[:12]}"
            payload = {
                "parent_goal": main_goal,
                "step_index": idx,
                "task_title": st.get("title", f"Step {idx}"),
                "instruction": st.get("instruction", ""),
                "target_module": st.get("target_module", "core")
            }
            job = self.manager.submit_job(idempotency_key=key, payload=payload, timeout_seconds=300.0)
            submitted_ids.append(job["job_id"])
        return submitted_ids

    def schedule_next_run(self) -> Dict[str, Any]:
        """Aloca o próximo job pendente a uma thread livre."""
        pending = self.manager.list_pending_jobs(limit=1)
        if not pending:
            return {"status": "EMPTY_QUEUE"}

        target_job = pending[0]
        thread = self.get_available_thread()
        if not thread:
            return {"status": "ALL_THREADS_BUSY"}

        # Claim the job
        claimed = self.manager.claim_job(target_job["job_id"], worker_id=thread["thread_id"])
        
        # Update registry
        threads = json.loads(self.thread_registry_file.read_text(encoding="utf-8"))
        for t in threads:
            if t["thread_id"] == thread["thread_id"]:
                t["status"] = "BUSY"
                t["assigned_job"] = target_job["job_id"]
                t["last_active"] = time.time()
                break
        self.thread_registry_file.write_text(json.dumps(threads, indent=2), encoding="utf-8")

        return {
            "status": "DISPATCHED",
            "job_id": target_job["job_id"],
            "thread_id": thread["thread_id"],
            "lease_epoch": claimed["lease_epoch"]
        }
