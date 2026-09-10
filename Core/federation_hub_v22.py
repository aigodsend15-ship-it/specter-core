from __future__ import annotations
import json, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from specter_mesh_protocol import verify_envelope

class FederationHub:
    """Durable owner-controlled HUB for peers, meetings and SMP/1 messages."""
    def __init__(self, db_path: str | Path):
        self.path=Path(db_path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(self.path),timeout=10,check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS peers(peer_id TEXT PRIMARY KEY,kind TEXT NOT NULL,endpoint TEXT,capabilities_json TEXT NOT NULL,status TEXT NOT NULL,last_seen REAL NOT NULL,evidence_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(message_id TEXT PRIMARY KEY,content_sha256 TEXT NOT NULL UNIQUE,sender TEXT NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,envelope_json TEXT NOT NULL,received_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS meetings(meeting_id TEXT PRIMARY KEY,objective TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,closed_at REAL);
        CREATE TABLE IF NOT EXISTS meeting_members(meeting_id TEXT NOT NULL,peer_id TEXT NOT NULL,role TEXT NOT NULL,joined_at REAL NOT NULL,PRIMARY KEY(meeting_id,peer_id));
        """); self.db.commit()
    def close(self):
        with self.lock: self.db.close()
    def register_peer(self,peer_id:str,kind:str,capabilities:Iterable[str],*,endpoint:Optional[str]=None,evidence:Optional[Dict[str,Any]]=None,status:str="ACTIVE")->Dict[str,Any]:
        now=time.time(); caps=sorted(set(str(x) for x in capabilities if str(x).strip())); ev=evidence or {}
        with self.lock,self.db:
            self.db.execute("INSERT INTO peers VALUES(?,?,?,?,?,?,?) ON CONFLICT(peer_id) DO UPDATE SET kind=excluded.kind,endpoint=excluded.endpoint,capabilities_json=excluded.capabilities_json,status=excluded.status,last_seen=excluded.last_seen,evidence_json=excluded.evidence_json",(peer_id,kind,endpoint,json.dumps(caps),status,now,json.dumps(ev,ensure_ascii=False)))
        return {"peer_id":peer_id,"status":status,"capabilities":caps,"last_seen":now}
    def heartbeat(self,peer_id:str,status:str="ACTIVE")->None:
        with self.lock,self.db:
            cur=self.db.execute("UPDATE peers SET status=?,last_seen=? WHERE peer_id=?",(status,time.time(),peer_id))
            if cur.rowcount!=1: raise KeyError(peer_id)
    def ingest(self,envelope:Dict[str,Any])->Dict[str,Any]:
        if not verify_envelope(envelope): raise ValueError("INVALID_SMP_ENVELOPE")
        mid=str(envelope["message_id"]); sha=str(envelope["content_sha256"])
        with self.lock:
            row=self.db.execute("SELECT content_sha256 FROM messages WHERE message_id=?",(mid,)).fetchone()
            if row:
                if row[0]!=sha: raise RuntimeError("MESSAGE_ID_CONFLICT")
                return {"message_id":mid,"deduplicated":True}
            with self.db:
                self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)",(mid,sha,str(envelope["from"]),str(envelope["to"]),str(envelope["kind"]),json.dumps(envelope,ensure_ascii=False),time.time()))
        return {"message_id":mid,"deduplicated":False}
    def open_meeting(self,objective:str,members:Dict[str,str])->Dict[str,Any]:
        meeting_id="meet-"+uuid.uuid4().hex[:16]; now=time.time()
        with self.lock,self.db:
            self.db.execute("INSERT INTO meetings VALUES(?,?,?,?,NULL)",(meeting_id,objective,"OPEN",now))
            for peer_id,role in sorted(members.items()): self.db.execute("INSERT INTO meeting_members VALUES(?,?,?,?)",(meeting_id,peer_id,role,now))
        return {"meeting_id":meeting_id,"objective":objective,"members":members,"status":"OPEN"}
    def close_meeting(self,meeting_id:str)->None:
        with self.lock,self.db:
            cur=self.db.execute("UPDATE meetings SET status='CLOSED',closed_at=? WHERE meeting_id=? AND status='OPEN'",(time.time(),meeting_id))
            if cur.rowcount!=1: raise RuntimeError("MEETING_NOT_OPEN")
    def summary(self)->Dict[str,Any]:
        with self.lock:
            peers=[{"peer_id":r[0],"kind":r[1],"endpoint":r[2],"capabilities":json.loads(r[3]),"status":r[4],"last_seen":r[5],"evidence":json.loads(r[6])} for r in self.db.execute("SELECT * FROM peers ORDER BY peer_id")]
            meetings=dict(self.db.execute("SELECT status,COUNT(*) FROM meetings GROUP BY status").fetchall()); messages=dict(self.db.execute("SELECT kind,COUNT(*) FROM messages GROUP BY kind").fetchall())
        return {"peers":peers,"meetings":meetings,"messages":messages,"authority":"local-policy-and-receipts"}
