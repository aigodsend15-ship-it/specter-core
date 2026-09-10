from __future__ import annotations
import hashlib, json, sqlite3, time, uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

class ContextLineageManager:
    """Durable conversation-independent project memory with hash-chained checkpoints."""
    def __init__(self, path: str | Path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS branches(branch_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,parent_branch TEXT,created_at REAL NOT NULL,status TEXT NOT NULL,head_checkpoint TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(checkpoint_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,branch_id TEXT NOT NULL,parent_checkpoint TEXT,parent_sha256 TEXT,event_seq INTEGER NOT NULL,created_at REAL NOT NULL,payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,chain_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY,provider TEXT NOT NULL,conversation_key TEXT,branch_id TEXT NOT NULL,state TEXT NOT NULL,turns INTEGER NOT NULL DEFAULT 0,chars INTEGER NOT NULL DEFAULT 0,last_warning TEXT,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS merges(merge_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,target_branch TEXT NOT NULL,input_json TEXT NOT NULL,checkpoint_id TEXT NOT NULL,created_at REAL NOT NULL);
        """); self.db.commit()

    def close(self):
        self.db.close()

    def ensure_branch(self, project_id: str, branch_id: str = "main", parent_branch: Optional[str] = None) -> str:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO branches VALUES(?,?,?,?,?,?)", (branch_id, project_id, parent_branch, time.time(), "ACTIVE", None))
        return branch_id

    def create_checkpoint(self, project_id: str, branch_id: str, payload: Dict[str, Any], parent_checkpoint: Optional[str] = None, event_seq: int = 0) -> Dict[str, Any]:
        self.ensure_branch(project_id, branch_id)
        if parent_checkpoint is None:
            row = self.db.execute("SELECT head_checkpoint FROM branches WHERE branch_id=?", (branch_id,)).fetchone(); parent_checkpoint = row[0] if row else None
        parent_sha = "GENESIS"
        if parent_checkpoint:
            row = self.db.execute("SELECT chain_sha256 FROM checkpoints WHERE checkpoint_id=?", (parent_checkpoint,)).fetchone()
            if not row: raise KeyError("PARENT_CHECKPOINT_NOT_FOUND")
            parent_sha = row[0]
        body = _canon(payload); payload_sha = _sha(body)
        chain_sha = _sha(_canon({"parent": parent_sha, "payload_sha256": payload_sha, "event_seq": int(event_seq), "branch_id": branch_id}))
        checkpoint_id = "cp-" + uuid.uuid4().hex[:16]
        with self.db:
            self.db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)", (checkpoint_id, project_id, branch_id, parent_checkpoint, parent_sha, int(event_seq), time.time(), body, payload_sha, chain_sha))
            self.db.execute("UPDATE branches SET head_checkpoint=?,status='ACTIVE' WHERE branch_id=?", (checkpoint_id, branch_id))
        return {"checkpoint_id": checkpoint_id, "branch_id": branch_id, "payload_sha256": payload_sha, "chain_sha256": chain_sha, "event_seq": int(event_seq)}

    def fork_branch(self, project_id: str, parent_branch: str, parent_checkpoint: Optional[str] = None, branch_id: Optional[str] = None) -> str:
        if parent_checkpoint is None:
            row = self.db.execute("SELECT head_checkpoint FROM branches WHERE branch_id=?", (parent_branch,)).fetchone(); parent_checkpoint = row[0] if row else None
        bid = branch_id or ("branch-" + uuid.uuid4().hex[:12])
        with self.db:
            self.db.execute("INSERT INTO branches VALUES(?,?,?,?,?,?)", (bid, project_id, parent_branch, time.time(), "ACTIVE", parent_checkpoint))
        return bid

    def get_checkpoint(self, checkpoint_id: str) -> Dict[str, Any]:
        row = self.db.execute("SELECT project_id,branch_id,parent_checkpoint,event_seq,payload_json,payload_sha256,chain_sha256 FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone()
        if not row: raise KeyError(checkpoint_id)
        return {"checkpoint_id": checkpoint_id, "project_id": row[0], "branch_id": row[1], "parent_checkpoint": row[2], "event_seq": row[3], "payload": json.loads(row[4]), "payload_sha256": row[5], "chain_sha256": row[6]}

    def resume_capsule(self, checkpoint_id: str, max_chars: int = 12000) -> str:
        cp = self.get_checkpoint(checkpoint_id)
        capsule = {"schema": "specter.resume.v2", "project_id": cp["project_id"], "branch_id": cp["branch_id"], "checkpoint_id": checkpoint_id, "parent_checkpoint": cp["parent_checkpoint"], "event_seq": cp["event_seq"], "chain_sha256": cp["chain_sha256"], "state": cp["payload"]}
        raw = _canon(capsule)
        if len(raw) <= max_chars: return raw
        p = cp["payload"]
        compact = {"objective": p.get("objective"), "current_plan": p.get("current_plan", [])[:20], "open_tasks": p.get("open_tasks", [])[:30], "artifacts": p.get("artifacts", [])[:30], "recent_receipts": p.get("recent_receipts", [])[-20:], "invariants": p.get("invariants", {}), "next_action": p.get("next_action")}
        capsule["state"] = compact; capsule["compacted"] = True
        return _canon(capsule)[:max_chars]

    def register_session(self, session_id: str, provider: str, conversation_key: Optional[str], branch_id: str, state: str = "ACTIVE") -> None:
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET provider=excluded.provider,conversation_key=excluded.conversation_key,branch_id=excluded.branch_id,state=excluded.state,updated_at=excluded.updated_at", (session_id, provider, conversation_key, branch_id, state, 0, 0, None, time.time()))

    def report_session(self, session_id: str, turns: int, chars: int, warning: Optional[str] = None) -> str:
        state = "CONTEXT_EXHAUSTED" if warning and "maximum" in warning.lower() else ("CONTEXT_PRESSURE" if warning or turns >= 180 or chars >= 650000 else "ACTIVE")
        with self.db:
            self.db.execute("UPDATE sessions SET turns=?,chars=?,last_warning=?,state=?,updated_at=? WHERE session_id=?", (int(turns), int(chars), warning, state, time.time(), session_id))
        return state

    def merge(self, project_id: str, target_branch: str, checkpoint_ids: Iterable[str], synthesis: Dict[str, Any], event_seq: int = 0) -> Dict[str, Any]:
        inputs = [self.get_checkpoint(c) for c in checkpoint_ids]
        payload = dict(synthesis); payload["merge_inputs"] = [{"checkpoint_id": c["checkpoint_id"], "chain_sha256": c["chain_sha256"], "branch_id": c["branch_id"]} for c in inputs]
        cp = self.create_checkpoint(project_id, target_branch, payload, event_seq=event_seq)
        mid = "merge-" + uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("INSERT INTO merges VALUES(?,?,?,?,?,?)", (mid, project_id, target_branch, _canon(payload["merge_inputs"]), cp["checkpoint_id"], time.time()))
        return {"merge_id": mid, **cp}
