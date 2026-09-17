from __future__ import annotations
import hashlib, json, sqlite3, time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

CRITICAL_ROLES = {"SECURITY", "VERIFIER"}

class DeliberationEngine:
    """Evidence-first aggregation for independent agent outputs."""
    def __init__(self, path: str | Path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS findings(
          finding_id TEXT PRIMARY KEY, topic TEXT NOT NULL, role TEXT NOT NULL,
          claim TEXT NOT NULL, evidence_json TEXT NOT NULL, confidence REAL NOT NULL,
          verdict TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_find_topic ON findings(topic,created_at);
        """)
        self.db.commit()

    def close(self): self.db.close()

    @staticmethod
    def _id(topic: str, role: str, claim: str) -> str:
        raw=f"{topic}|{role}|{claim}".encode("utf-8")
        return "find-"+hashlib.sha256(raw).hexdigest()[:16]
    def add(self, topic: str, role: str, claim: str, evidence: Iterable[Any], confidence: float, verdict: str="PROPOSED") -> str:
        fid=self._id(topic,role,claim)
        ev=list(evidence or [])
        conf=max(0.0,min(1.0,float(confidence)))
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO findings VALUES(?,?,?,?,?,?,?,?)",
                            (fid,topic,role,claim,json.dumps(ev,ensure_ascii=False),conf,verdict,time.time()))
        return fid

    def set_verdict(self, finding_id: str, verdict: str) -> None:
        with self.db:
            self.db.execute("UPDATE findings SET verdict=? WHERE finding_id=?",(verdict,finding_id))

    def topic(self, topic: str):
        rows=self.db.execute("SELECT finding_id,role,claim,evidence_json,confidence,verdict,created_at FROM findings WHERE topic=? ORDER BY confidence DESC,created_at ASC",(topic,)).fetchall()
        return [{"finding_id":r[0],"role":r[1],"claim":r[2],"evidence":json.loads(r[3]),"confidence":float(r[4]),"verdict":r[5],"created_at":float(r[6])} for r in rows]

    def synthesis_gate(self, topic: str, min_independent_roles: int=2) -> Dict[str,Any]:
        items=self.topic(topic)
        roles={x["role"] for x in items if x["verdict"]!="REJECTED"}
        verified=any(x["role"]=="VERIFIER" and x["verdict"]=="APPROVED" for x in items)
        security_reject=any(x["role"]=="SECURITY" and x["verdict"]=="REJECTED" for x in items)
        evidence_count=sum(len(x["evidence"]) for x in items if x["verdict"]!="REJECTED")
        promote=(len(roles)>=int(min_independent_roles) and verified and not security_reject and evidence_count>0)
        return {"topic":topic,"promote":promote,"independent_roles":len(roles),"verified":verified,"security_reject":security_reject,"evidence_count":evidence_count,"findings":items}
