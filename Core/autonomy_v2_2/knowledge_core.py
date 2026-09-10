from __future__ import annotations
import hashlib, json, sqlite3, time, uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

CLASSES={"OBSERVATION","VERIFIED_FACT","PREFERENCE","POLICY","SECRET_REF","EPHEMERAL"}

class KnowledgeCore:
    """Provenance-aware local memory. External data cannot promote itself to POLICY."""
    def __init__(self,path: str | Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.path),timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memories(
          memory_id TEXT PRIMARY KEY,class TEXT NOT NULL,content TEXT NOT NULL,
          content_sha TEXT NOT NULL,source_ref TEXT,evidence_ref TEXT,policy_hash TEXT,
          confidence REAL NOT NULL,created_at REAL NOT NULL,expires_at REAL,metadata_json TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedup ON memories(class,content_sha,COALESCE(source_ref,''));
        """)
        self.db.commit()

    def close(self): self.db.close()

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(str(text).encode("utf-8")).hexdigest()
    def add(self,memory_class: str,content: str,source_ref: Optional[str]=None,evidence_ref: Optional[str]=None,
            policy_hash: Optional[str]=None,confidence: float=0.5,ttl_s: Optional[float]=None,
            metadata: Optional[Dict[str,Any]]=None,authority_external: bool=False) -> str:
        cls=str(memory_class).upper()
        if cls not in CLASSES: raise ValueError("UNKNOWN_MEMORY_CLASS")
        if cls=="POLICY" and not authority_external: raise PermissionError("POLICY_REQUIRES_EXTERNAL_AUTHORITY")
        if cls=="VERIFIED_FACT" and not evidence_ref: raise ValueError("VERIFIED_FACT_REQUIRES_EVIDENCE")
        if cls=="SECRET_REF" and any(x in str(content).lower() for x in ("-----begin","password=","seed phrase")):
            raise ValueError("SECRET_REF_MUST_NOT_CONTAIN_SECRET")
        now=time.time(); exp=(now+float(ttl_s)) if ttl_s else None; digest=self._sha(content)
        row=self.db.execute("SELECT memory_id FROM memories WHERE class=? AND content_sha=? AND COALESCE(source_ref,'')=COALESCE(?, '')",(cls,digest,source_ref)).fetchone()
        if row: return str(row[0])
        mid="mem-"+uuid.uuid4().hex[:16]
        with self.db:
            self.db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)",(
                mid,cls,str(content),digest,source_ref,evidence_ref,policy_hash,
                max(0.0,min(1.0,float(confidence))),now,exp,json.dumps(metadata or {},ensure_ascii=False)))
        return mid

    def verify(self,memory_id: str,evidence_ref: str,confidence: float=1.0) -> str:
        row=self.db.execute("SELECT content,source_ref FROM memories WHERE memory_id=?",(memory_id,)).fetchone()
        if not row: raise KeyError(memory_id)
        return self.add("VERIFIED_FACT",row[0],row[1],evidence_ref=evidence_ref,confidence=confidence)
    def search(self,query: str,limit: int=20,classes: Optional[Iterable[str]]=None) -> list[Dict[str,Any]]:
        now=time.time(); allowed={str(x).upper() for x in (classes or CLASSES)}
        rows=self.db.execute("SELECT memory_id,class,content,source_ref,evidence_ref,confidence,created_at,expires_at,metadata_json FROM memories WHERE (expires_at IS NULL OR expires_at>?) ORDER BY confidence DESC,created_at DESC",(now,)).fetchall()
        q=str(query).lower(); out=[]
        for r in rows:
            if r[1] not in allowed or q not in r[2].lower(): continue
            out.append({"memory_id":r[0],"class":r[1],"content":r[2],"source_ref":r[3],"evidence_ref":r[4],"confidence":r[5],"created_at":r[6],"expires_at":r[7],"metadata":json.loads(r[8])})
            if len(out)>=max(1,int(limit)): break
        return out

    def stats(self) -> Dict[str,int]:
        return {str(k):int(v) for k,v in self.db.execute("SELECT class,COUNT(*) FROM memories GROUP BY class").fetchall()}
