from __future__ import annotations
import hashlib, hmac, json, secrets, sqlite3, time, uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from .mesh_protocol import verify_envelope

class FederationHub:
    """Concurrent owner-controlled federation store with atomic leases and receipts."""
    def __init__(self,path: str | Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS enrollments(token_hash TEXT PRIMARY KEY,node_hint TEXT,expires_at REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS nodes(node_id TEXT PRIMARY KEY,name TEXT NOT NULL,capabilities_json TEXT NOT NULL,status TEXT NOT NULL,last_seen REAL NOT NULL,address TEXT NOT NULL,secret TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,capability TEXT NOT NULL,payload_json TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,lease_node TEXT,lease_token INTEGER NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,result_json TEXT);
            CREATE TABLE IF NOT EXISTS receipts(seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,node_id TEXT NOT NULL,lease_token INTEGER NOT NULL,ts REAL NOT NULL,receipt_json TEXT NOT NULL,receipt_sha TEXT NOT NULL,prev_receipt_sha TEXT);
            CREATE TABLE IF NOT EXISTS messages(message_id TEXT PRIMARY KEY,content_sha256 TEXT NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,envelope_json TEXT NOT NULL,received_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(message_id TEXT NOT NULL,recipient_peer_id TEXT NOT NULL,acked INTEGER NOT NULL DEFAULT 0,acked_at REAL,PRIMARY KEY(message_id,recipient_peer_id));
            """)
            db.commit()

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(str(self.path),timeout=15,isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL"); db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
        finally:
            db.close()

    def close(self): return None

    @staticmethod
    def _hash(value: str) -> str: return hashlib.sha256(value.encode("utf-8")).hexdigest()
    def create_enrollment(self,node_hint: str="",ttl_s: int=900) -> str:
        token=secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO enrollments VALUES(?,?,?,0)",(self._hash(token),node_hint,time.time()+max(60,int(ttl_s))))
            db.commit()
        return token

    def enroll(self,token: str,name: str,capabilities: Iterable[str],address: str="") -> Dict[str,Any]:
        th=self._hash(token); now=time.time(); secret=secrets.token_urlsafe(48)
        caps=tuple(sorted({str(x) for x in capabilities if str(x)}))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT node_hint,expires_at,used FROM enrollments WHERE token_hash=?",(th,)).fetchone()
            if not row or float(row[1])<now or int(row[2]): db.rollback(); raise PermissionError("INVALID_ENROLLMENT")
            node_id=(str(row[0]).strip() or ("node-"+uuid.uuid4().hex[:12]))
            cur=db.execute("UPDATE enrollments SET used=1 WHERE token_hash=? AND used=0",(th,))
            if cur.rowcount!=1: db.rollback(); raise PermissionError("ENROLLMENT_RACE")
            db.execute("INSERT OR REPLACE INTO nodes VALUES(?,?,?,?,?,?,?)",(node_id,name,json.dumps(caps),"ACTIVE",now,address,secret)); db.commit()
        return {"node_id":node_id,"node_secret":secret,"capabilities":caps}

    def _node_secret(self,node_id: str) -> str:
        with self._connect() as db: row=db.execute("SELECT secret FROM nodes WHERE node_id=?",(node_id,)).fetchone()
        if not row: raise KeyError(node_id)
        return str(row[0])

    def sign(self,node_id: str,payload: Dict[str,Any]) -> str:
        body=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
        return hmac.new(self._node_secret(node_id).encode("utf-8"),body,hashlib.sha256).hexdigest()
    def verify(self,node_id: str,payload: Dict[str,Any],signature: str) -> bool:
        try: expected=self.sign(node_id,payload)
        except Exception: return False
        return hmac.compare_digest(expected,str(signature))

    def heartbeat(self,node_id: str,address: str="") -> None:
        with self._connect() as db:
            cur=db.execute("UPDATE nodes SET status='ACTIVE',last_seen=?,address=CASE WHEN ?='' THEN address ELSE ? END WHERE node_id=?",(time.time(),address,address,node_id)); db.commit()
        if not cur.rowcount: raise KeyError(node_id)

    def submit(self,capability: str,payload: Dict[str,Any],idempotency_key: Optional[str]=None) -> str:
        key=idempotency_key or self._hash(capability+"|"+json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False))
        with self._connect() as db:
            row=db.execute("SELECT job_id FROM jobs WHERE idempotency_key=?",(key,)).fetchone()
            if row: return str(row[0])
            jid="fedjob-"+uuid.uuid4().hex[:16]; now=time.time()
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO jobs(job_id,idempotency_key,capability,payload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(jid,key,capability,json.dumps(payload,ensure_ascii=False),"QUEUED",now,now)); db.commit()
                return jid
            except sqlite3.IntegrityError:
                db.rollback(); row=db.execute("SELECT job_id FROM jobs WHERE idempotency_key=?",(key,)).fetchone(); return str(row[0])

    def _capabilities(self,db,node_id: str) -> set[str]:
        row=db.execute("SELECT capabilities_json FROM nodes WHERE node_id=?",(node_id,)).fetchone()
        if not row: raise KeyError(node_id)
        return set(json.loads(row[0]))
    def claim(self,node_id: str,ttl_s: int=120) -> Optional[Dict[str,Any]]:
        now=time.time(); ttl=max(30,int(ttl_s))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            caps=self._capabilities(db,node_id)
            rows=db.execute("SELECT job_id,capability,payload_json,lease_token,state FROM jobs WHERE state IN ('QUEUED','RETRY') AND lease_until<=? ORDER BY created_at",(now,)).fetchall()
            for jid,cap,payload,token,state in rows:
                if cap not in caps and "*" not in caps: continue
                next_token=int(token)+1
                cur=db.execute("UPDATE jobs SET state='RUNNING',lease_node=?,lease_token=?,lease_until=?,updated_at=? WHERE job_id=? AND state=? AND lease_until<=?",(node_id,next_token,now+ttl,now,jid,state,now))
                if cur.rowcount==1:
                    db.commit()
                    return {"job_id":jid,"capability":cap,"payload":json.loads(payload),"lease_token":next_token,"lease_until":now+ttl}
            db.commit(); return None

    def renew(self,node_id: str,job_id: str,lease_token: int,ttl_s: int=120) -> float:
        now=time.time(); until=now+max(30,int(ttl_s))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur=db.execute("UPDATE jobs SET lease_until=?,updated_at=? WHERE job_id=? AND state='RUNNING' AND lease_node=? AND lease_token=? AND lease_until>?",(until,now,job_id,node_id,int(lease_token),now))
            if cur.rowcount!=1: db.rollback(); raise RuntimeError("STALE_OR_INVALID_FEDERATION_LEASE")
            db.commit(); return until

    def complete(self,node_id: str,job_id: str,lease_token: int,result: Dict[str,Any]) -> Dict[str,Any]:
        now=time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT capability,payload_json,lease_node,lease_token,lease_until,state,result_json FROM jobs WHERE job_id=?",(job_id,)).fetchone()
            if not row: db.rollback(); raise KeyError(job_id)
            if row[5]=='DONE':
                prev=db.execute("SELECT receipt_json,receipt_sha FROM receipts WHERE job_id=? ORDER BY seq DESC LIMIT 1",(job_id,)).fetchone()
                if prev: db.commit(); return {**json.loads(prev[0]),"receipt_sha256":prev[1],"replayed_terminal_receipt":True}
            if row[2]!=node_id or int(row[3])!=int(lease_token) or float(row[4])<=now or row[5]!='RUNNING':
                db.rollback(); raise RuntimeError("STALE_OR_INVALID_FEDERATION_LEASE")
            input_sha=self._hash(str(row[0])+"|"+str(row[1]))
            output_json=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(",",":"))
            output_sha=self._hash(output_json)
            prev=db.execute("SELECT receipt_sha FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
            prev_sha=str(prev[0]) if prev else None
            receipt={"schema":"specter.federation.receipt.v2","job_id":job_id,"node_id":node_id,"lease_token":int(lease_token),"capability":row[0],"input_sha256":input_sha,"output_sha256":output_sha,"result":result,"ts":now,"prev_receipt_sha256":prev_sha}
            raw=json.dumps(receipt,sort_keys=True,separators=(",",":"),ensure_ascii=False); digest=self._hash(raw)
            cur=db.execute("UPDATE jobs SET state='DONE',result_json=?,lease_until=0,updated_at=? WHERE job_id=? AND state='RUNNING' AND lease_node=? AND lease_token=?",(output_json,now,job_id,node_id,int(lease_token)))
            if cur.rowcount!=1: db.rollback(); raise RuntimeError("FEDERATION_COMPLETE_RACE")
            db.execute("INSERT INTO receipts(job_id,node_id,lease_token,ts,receipt_json,receipt_sha,prev_receipt_sha) VALUES(?,?,?,?,?,?,?)",(job_id,node_id,int(lease_token),now,raw,digest,prev_sha)); db.commit()
        return {**receipt,"receipt_sha256":digest,"replayed_terminal_receipt":False}

    def reconcile(self,stale_after_s: int=180) -> int:
        now=time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows=db.execute("SELECT job_id FROM jobs WHERE state='RUNNING' AND lease_until<=?",(now,)).fetchall()
            db.execute("UPDATE jobs SET state='RETRY',lease_node=NULL,lease_until=0,updated_at=? WHERE state='RUNNING' AND lease_until<=?",(now,now))
            db.execute("UPDATE nodes SET status='STALE' WHERE last_seen<?",(now-max(30,int(stale_after_s)),)); db.commit()
        return len(rows)

    def ingest_message(self,authenticated_peer_id: str,envelope: Dict[str,Any],recipients: Optional[Iterable[str]]=None) -> Dict[str,Any]:
        if not verify_envelope(envelope): raise ValueError("INVALID_SMP_ENVELOPE")
        if str(envelope.get("from"))!=str(authenticated_peer_id): raise PermissionError("SMP_SENDER_IDENTITY_MISMATCH")
        mid=str(envelope["message_id"]); digest=str(envelope["content_sha256"]); target=str(envelope["to"]); now=time.time()
        targets=sorted({str(x) for x in (recipients or []) if str(x)}) if target=="*" else [target]
        if target=="*" and not targets: raise ValueError("BROADCAST_RECIPIENTS_REQUIRED")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT content_sha256 FROM messages WHERE message_id=?",(mid,)).fetchone()
            if row and str(row[0])!=digest: db.rollback(); raise RuntimeError("REPLAY_CONFLICT")
            dedup=bool(row)
            if not row:
                db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)",(mid,digest,str(envelope["from"]),target,str(envelope["kind"]),json.dumps(envelope,ensure_ascii=False,sort_keys=True,separators=(",",":")),now))
            for peer in targets:
                if not db.execute("SELECT 1 FROM nodes WHERE node_id=?",(peer,)).fetchone(): db.rollback(); raise KeyError("UNKNOWN_RECIPIENT:"+peer)
                db.execute("INSERT OR IGNORE INTO deliveries(message_id,recipient_peer_id) VALUES(?,?)",(mid,peer))
            db.commit()
        return {"message_id":mid,"deduplicated":dedup,"deliveries":len(targets)}

    def pending_messages(self,authenticated_peer_id: str,limit: int=50) -> list[Dict[str,Any]]:
        with self._connect() as db:
            rows=db.execute("SELECT m.envelope_json FROM messages m JOIN deliveries d ON d.message_id=m.message_id WHERE d.recipient_peer_id=? AND d.acked=0 ORDER BY m.received_at LIMIT ?",(authenticated_peer_id,max(1,min(200,int(limit))))).fetchall()
        return [json.loads(r[0]) for r in rows]

    def ack_message(self,authenticated_peer_id: str,message_id: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur=db.execute("UPDATE deliveries SET acked=1,acked_at=? WHERE message_id=? AND recipient_peer_id=? AND acked=0",(time.time(),str(message_id),str(authenticated_peer_id)))
            db.commit(); return cur.rowcount==1

    def summary(self) -> Dict[str,Any]:
        with self._connect() as db:
            nodes={str(k):int(v) for k,v in db.execute("SELECT status,COUNT(*) FROM nodes GROUP BY status").fetchall()}
            jobs={str(k):int(v) for k,v in db.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state").fetchall()}
            receipts=int(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]); messages=int(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]); pending=int(db.execute("SELECT COUNT(*) FROM deliveries WHERE acked=0").fetchone()[0])
        return {"nodes":nodes,"jobs":jobs,"receipts":receipts,"messages":messages,"pending_deliveries":pending,"transport":"overlay-ready","default_exposure":"loopback-only"}
