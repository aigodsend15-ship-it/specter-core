from __future__ import annotations
import re, sqlite3, time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROLE_RE=re.compile(r"\bROLE\s+([A-Z][A-Z0-9_]{2,40})\b")

class ConversationMesh:
    """Durable role->conversation identity; browser target IDs are transient bindings."""
    def __init__(self,path: str | Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.path),timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS bindings(
          role TEXT PRIMARY KEY,provider TEXT NOT NULL,profile_scope TEXT NOT NULL,
          conversation_url TEXT NOT NULL,tab_id INTEGER,state TEXT NOT NULL,updated_at REAL NOT NULL)""")
        self.db.commit()

    def close(self): self.db.close()

    def bind(self,role: str,provider: str,profile_scope: str,url: str,tab_id: Optional[int]=None,state: str="BOUND"):
        role=str(role).upper().strip(); now=time.time()
        with self.db:
            self.db.execute("INSERT INTO bindings VALUES(?,?,?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET provider=excluded.provider,profile_scope=excluded.profile_scope,conversation_url=excluded.conversation_url,tab_id=excluded.tab_id,state=excluded.state,updated_at=excluded.updated_at",(role,provider,profile_scope,url,tab_id,state,now))

    def infer_role(self,text: str) -> Optional[str]:
        m=ROLE_RE.search(str(text or "")); return m.group(1) if m else None
    def refresh_targets(self,tabs: Iterable[Dict[str,Any]]) -> int:
        rows=self.db.execute("SELECT role,conversation_url FROM bindings").fetchall(); by_url={r[1]:r[0] for r in rows}
        count=0; now=time.time()
        with self.db:
            for t in tabs:
                url=str(t.get("url") or ""); tid=t.get("id")
                role=by_url.get(url)
                if role and isinstance(tid,int):
                    self.db.execute("UPDATE bindings SET tab_id=?,state='BOUND',updated_at=? WHERE role=?",(tid,now,role)); count+=1
        return count

    def mark(self,role: str,state: str):
        with self.db: self.db.execute("UPDATE bindings SET state=?,updated_at=? WHERE role=?",(state,time.time(),str(role).upper()))

    def get(self,role: str) -> Optional[Dict[str,Any]]:
        r=self.db.execute("SELECT role,provider,profile_scope,conversation_url,tab_id,state,updated_at FROM bindings WHERE role=?",(str(role).upper(),)).fetchone()
        if not r: return None
        return {"role":r[0],"provider":r[1],"profile_scope":r[2],"conversation_url":r[3],"tab_id":r[4],"state":r[5],"updated_at":r[6]}

    def all(self) -> list[Dict[str,Any]]:
        return [{"role":r[0],"provider":r[1],"profile_scope":r[2],"conversation_url":r[3],"tab_id":r[4],"state":r[5],"updated_at":r[6]} for r in self.db.execute("SELECT role,provider,profile_scope,conversation_url,tab_id,state,updated_at FROM bindings ORDER BY role")]
