from __future__ import annotations
import hashlib, json, sqlite3, time, urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

TRUST={"github.com":0.9,"docs.python.org":0.9,"openai.com":0.9,"microsoft.com":0.9,"modelcontextprotocol.io":0.9,"browser-use.com":0.75}

def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()

class PublicResearchMiner:
    """Evidence-first public research ingestion. Network is opt-in and credential-free."""
    def __init__(self,path: str | Path,allow_network: bool=False):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.allow_network=bool(allow_network)
        self.db=sqlite3.connect(str(self.path),timeout=10); self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sources(source_id TEXT PRIMARY KEY,url TEXT UNIQUE,title TEXT,domain TEXT,sha256 TEXT NOT NULL,body TEXT NOT NULL,trust REAL NOT NULL,discovered_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS findings(finding_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,topic TEXT NOT NULL,claim TEXT NOT NULL,evidence TEXT NOT NULL,score REAL NOT NULL,created_at REAL NOT NULL);
        """); self.db.commit()

    def close(self): self.db.close()
    def ingest(self,url: str,body: str,title: str="",trust: Optional[float]=None) -> str:
        from urllib.parse import urlparse
        domain=(urlparse(url).hostname or "").lower(); sid="src-"+_sha(url.encode())[:16]; digest=_sha(body.encode("utf-8",errors="replace")); t=float(TRUST.get(domain,0.5) if trust is None else trust)
        with self.db:
            self.db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,sha256=excluded.sha256,body=excluded.body,trust=excluded.trust,discovered_at=excluded.discovered_at",(sid,url,title,domain,digest,body,t,time.time()))
        return sid

    def fetch_public(self,url: str,timeout_s: int=12,max_bytes: int=1000000) -> str:
        if not self.allow_network: raise RuntimeError("NETWORK_DISABLED")
        req=urllib.request.Request(url,headers={"User-Agent":"SpecterResearchMiner/2.0 public-research"})
        with urllib.request.urlopen(req,timeout=timeout_s) as r: data=r.read(max_bytes+1)
        if len(data)>max_bytes: data=data[:max_bytes]
        return self.ingest(url,data.decode("utf-8",errors="replace"),title=url)

    def add_finding(self,source_id: str,topic: str,claim: str,evidence: str,score: float) -> str:
        row=self.db.execute("SELECT trust FROM sources WHERE source_id=?",(source_id,)).fetchone()
        if not row: raise KeyError(source_id)
        final=max(0.0,min(1.0,float(score)*float(row[0]))); fid="finding-"+_sha((source_id+topic+claim).encode())[:16]
        with self.db: self.db.execute("INSERT OR REPLACE INTO findings VALUES(?,?,?,?,?,?,?)",(fid,source_id,topic,claim,evidence,final,time.time()))
        return fid

    def query(self,topic: str,limit: int=20) -> List[Dict[str,Any]]:
        rows=self.db.execute("SELECT f.finding_id,s.url,s.title,f.claim,f.evidence,f.score FROM findings f JOIN sources s ON s.source_id=f.source_id WHERE f.topic=? ORDER BY f.score DESC,f.created_at DESC LIMIT ?",(topic,int(limit))).fetchall()
        return [{"finding_id":r[0],"url":r[1],"title":r[2],"claim":r[3],"evidence":r[4],"score":r[5]} for r in rows]

    def dedup_stats(self) -> Dict[str,int]:
        return {"sources":self.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],"findings":self.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0]}
