from __future__ import annotations
import json, sqlite3, threading, time, uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

DEFAULT_ROLES=("COORDINATOR","ARCHITECT","WEB_ADAPTER","LOCAL_EXECUTOR","SECURITY","GITHUB_SCOUT","VERIFIER","CONTEXT_LINEAGE","RELIABILITY","RELEASE_FUNDING")
TERMINAL={"DONE","FAILED","CANCELLED"}

class DurableAgentFabric:
    """Ten-role durable task fabric with atomic leases, fencing and deduplication."""
    def __init__(self,path: str | Path,roles: Iterable[str]=DEFAULT_ROLES):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock()
        self.db=sqlite3.connect(str(self.path),timeout=15,check_same_thread=False,isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL"); self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS agents(role TEXT PRIMARY KEY,state TEXT NOT NULL,endpoint_id TEXT,last_heartbeat REAL NOT NULL,current_task TEXT);
        CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY,dedup_key TEXT UNIQUE,role TEXT NOT NULL,priority INTEGER NOT NULL,state TEXT NOT NULL,payload_json TEXT NOT NULL,result_json TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_token INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS task_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,ts REAL NOT NULL,kind TEXT NOT NULL,payload_json TEXT NOT NULL);
        """)
        now=time.time()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            for role in roles: self.db.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?,?,?)",(role,"IDLE",None,now,None))
            self.db.commit()

    def close(self):
        with self.lock: self.db.close()
    def roles(self):
        with self.lock: return [r[0] for r in self.db.execute("SELECT role FROM agents ORDER BY role")]

    def bind_endpoint(self,role: str,endpoint_id: Optional[str],state: str="IDLE"):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("UPDATE agents SET endpoint_id=?,state=?,last_heartbeat=? WHERE role=?",(endpoint_id,state,time.time(),role)); self.db.commit()

    def heartbeat(self,role: str,state: Optional[str]=None):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            if state is None: self.db.execute("UPDATE agents SET last_heartbeat=? WHERE role=?",(time.time(),role))
            else: self.db.execute("UPDATE agents SET last_heartbeat=?,state=? WHERE role=?",(time.time(),state,role))
            self.db.commit()

    def submit(self,role: str,payload: Dict[str,Any],dedup_key: Optional[str]=None,priority: int=100) -> str:
        if role not in self.roles(): raise KeyError("UNKNOWN_ROLE")
        key=dedup_key or (role+":"+json.dumps(payload,sort_keys=True,separators=(",",":")))
        now=time.time(); tid="task-"+uuid.uuid4().hex[:16]
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT task_id FROM tasks WHERE dedup_key=?",(key,)).fetchone()
            if row: self.db.commit(); return str(row[0])
            self.db.execute("INSERT INTO tasks(task_id,dedup_key,role,priority,state,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(tid,key,role,int(priority),"QUEUED",json.dumps(payload,ensure_ascii=False),now,now))
            self.db.execute("INSERT INTO task_events(task_id,ts,kind,payload_json) VALUES(?,?,?,?)",(tid,now,"queued",json.dumps({"role":role})))
            self.db.commit(); return tid
    def event(self,task_id: str,kind: str,payload: Dict[str,Any]):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO task_events(task_id,ts,kind,payload_json) VALUES(?,?,?,?)",(task_id,time.time(),kind,json.dumps(payload,ensure_ascii=False)))
            self.db.commit()

    def claim(self,role: str,owner: str,ttl_s: float=90.0) -> Optional[Dict[str,Any]]:
        now=time.time(); until=now+max(1.0,float(ttl_s))
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT task_id,payload_json,attempts,lease_token,state FROM tasks WHERE role=? AND state IN ('QUEUED','RETRY') AND lease_until<=? ORDER BY priority ASC,created_at ASC LIMIT 1",(role,now)).fetchone()
            if not row: self.db.commit(); return None
            tid,payload,attempts,token,state=row; next_token=int(token)+1
            cur=self.db.execute("UPDATE tasks SET state='RUNNING',attempts=?,lease_owner=?,lease_token=?,lease_until=?,updated_at=? WHERE task_id=? AND state=? AND lease_token=? AND lease_until<=?",(int(attempts)+1,owner,next_token,until,now,tid,state,int(token),now))
            if cur.rowcount!=1: self.db.rollback(); return None
            self.db.execute("UPDATE agents SET state='BUSY',current_task=?,last_heartbeat=? WHERE role=?",(tid,now,role))
            self.db.execute("INSERT INTO task_events(task_id,ts,kind,payload_json) VALUES(?,?,?,?)",(tid,now,"claimed",json.dumps({"owner":owner,"token":next_token})))
            self.db.commit()
        return {"task_id":tid,"role":role,"payload":json.loads(payload),"lease_token":next_token,"lease_until":until}

    def _lease_row(self,task_id: str):
        return self.db.execute("SELECT role,lease_owner,lease_token,lease_until,state FROM tasks WHERE task_id=?",(task_id,)).fetchone()

    def renew(self,task_id: str,owner: str,token: int,ttl_s: float=90.0):
        now=time.time(); until=now+max(1.0,float(ttl_s))
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            cur=self.db.execute("UPDATE tasks SET lease_until=?,updated_at=? WHERE task_id=? AND state='RUNNING' AND lease_owner=? AND lease_token=? AND lease_until>?",(until,now,task_id,owner,int(token),now))
            if cur.rowcount!=1: self.db.rollback(); raise RuntimeError("STALE_OR_INVALID_LEASE")
            self.db.commit(); return until
    def complete(self,task_id: str,owner: str,token: int,result: Dict[str,Any]):
        now=time.time(); result_json=json.dumps(result,ensure_ascii=False)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            row=self._lease_row(task_id)
            if not row or row[1]!=owner or int(row[2])!=int(token) or float(row[3])<=now or row[4]!="RUNNING":
                self.db.rollback(); raise RuntimeError("STALE_OR_INVALID_LEASE")
            role=row[0]
            cur=self.db.execute("UPDATE tasks SET state='DONE',result_json=?,lease_until=0,updated_at=? WHERE task_id=? AND state='RUNNING' AND lease_owner=? AND lease_token=? AND lease_until>?",(result_json,now,task_id,owner,int(token),now))
            if cur.rowcount!=1: self.db.rollback(); raise RuntimeError("COMPLETE_RACE")
            self.db.execute("UPDATE agents SET state='IDLE',current_task=NULL,last_heartbeat=? WHERE role=?",(now,role))
            self.db.execute("INSERT INTO task_events(task_id,ts,kind,payload_json) VALUES(?,?,?,?)",(task_id,now,"done",json.dumps({"owner":owner,"token":int(token)})))
            self.db.commit()
    def fail(self,task_id: str,owner: str,token: int,error: str,max_attempts: int=3):
        now=time.time()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT role,attempts,lease_owner,lease_token,lease_until,state FROM tasks WHERE task_id=?",(task_id,)).fetchone()
            if not row or row[2]!=owner or int(row[3])!=int(token) or float(row[4])<=now or row[5]!="RUNNING":
                self.db.rollback(); raise RuntimeError("STALE_OR_INVALID_LEASE")
            role,attempts=row[0],int(row[1]); state="FAILED" if attempts>=int(max_attempts) else "RETRY"
            cur=self.db.execute("UPDATE tasks SET state=?,result_json=?,lease_owner=NULL,lease_until=0,updated_at=? WHERE task_id=? AND state='RUNNING' AND lease_owner=? AND lease_token=?",(state,json.dumps({"error":error}),now,task_id,owner,int(token)))
            if cur.rowcount!=1: self.db.rollback(); raise RuntimeError("FAIL_RACE")
            self.db.execute("UPDATE agents SET state='IDLE',current_task=NULL,last_heartbeat=? WHERE role=?",(now,role))
            self.db.execute("INSERT INTO task_events(task_id,ts,kind,payload_json) VALUES(?,?,?,?)",(task_id,now,"failed_attempt",json.dumps({"state":state,"error":error[:1000],"token":int(token)})))
            self.db.commit()
    def reconcile_expired(self) -> int:
        now=time.time()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            rows=self.db.execute("SELECT task_id,role,lease_token FROM tasks WHERE state='RUNNING' AND lease_until<=?",(now,)).fetchall()
            for tid,role,token in rows:
                cur=self.db.execute("UPDATE tasks SET state='RETRY',lease_owner=NULL,lease_until=0,updated_at=? WHERE task_id=? AND state='RUNNING' AND lease_token=? AND lease_until<=?",(now,tid,int(token),now))
                if cur.rowcount==1:
                    self.db.execute("UPDATE agents SET state='IDLE',current_task=NULL,last_heartbeat=? WHERE role=? AND current_task=?",(now,role,tid))
                    self.db.execute("INSERT INTO task_events(task_id,ts,kind,payload_json) VALUES(?,?,?,?)",(tid,now,"lease_expired",json.dumps({"token":int(token)})))
            self.db.commit(); return len(rows)

    def summary(self) -> Dict[str,Any]:
        with self.lock:
            tasks=dict(self.db.execute("SELECT state,COUNT(*) FROM tasks GROUP BY state").fetchall())
            agents=[{"role":r[0],"state":r[1],"endpoint_id":r[2],"last_heartbeat":r[3],"current_task":r[4]} for r in self.db.execute("SELECT role,state,endpoint_id,last_heartbeat,current_task FROM agents ORDER BY role")]
            return {"roles":len(agents),"agents":agents,"tasks":tasks,"atomic_leases":True,"event_commit":"same_transaction"}
