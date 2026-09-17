from __future__ import annotations
import sqlite3, time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional

class CapacityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    WAITING_MODEL = "WAITING_MODEL"
    QUOTA_WAIT = "QUOTA_WAIT"
    CONTEXT_PRESSURE = "CONTEXT_PRESSURE"
    CONTEXT_EXHAUSTED = "CONTEXT_EXHAUSTED"
    OFFLINE = "OFFLINE"

@dataclass(frozen=True)
class Endpoint:
    endpoint_id: str
    provider: str
    account_scope: str
    quota_group: str
    state: CapacityState
    health: float
    concurrency: int
    active: int
    updated_at: float

class CapacityManager:
    """Quota-aware scheduler. It never rotates accounts to evade provider limits."""
    def __init__(self, path: str | Path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS endpoints(endpoint_id TEXT PRIMARY KEY,provider TEXT NOT NULL,account_scope TEXT NOT NULL,quota_group TEXT NOT NULL,state TEXT NOT NULL,health REAL NOT NULL,concurrency INTEGER NOT NULL,active INTEGER NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reservations(reservation_id TEXT PRIMARY KEY,endpoint_id TEXT NOT NULL,quota_group TEXT NOT NULL,owner TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,UNIQUE(endpoint_id,owner,state));
        CREATE INDEX IF NOT EXISTS idx_reservation_expiry ON reservations(state,expires_at);
        CREATE TABLE IF NOT EXISTS probe_leases(quota_group TEXT PRIMARY KEY,endpoint_id TEXT NOT NULL,owner TEXT NOT NULL,expires_at REAL NOT NULL);
        """); self.db.commit()

    def close(self): self.db.close()

    def register(self, endpoint_id: str, provider: str, account_scope: str, quota_group: Optional[str] = None, concurrency: int = 1) -> None:
        qg = quota_group or f"{provider}:{account_scope}"
        with self.db:
            self.db.execute("INSERT INTO endpoints VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(endpoint_id) DO UPDATE SET provider=excluded.provider,account_scope=excluded.account_scope,quota_group=excluded.quota_group,concurrency=excluded.concurrency,updated_at=excluded.updated_at", (endpoint_id,provider,account_scope,qg,CapacityState.AVAILABLE.value,1.0,max(1,int(concurrency)),0,time.time()))

    def report(self, endpoint_id: str, state: CapacityState | str, health: Optional[float] = None, active: Optional[int] = None) -> None:
        st = CapacityState(state).value
        row = self.db.execute("SELECT health,active FROM endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
        if not row: raise KeyError(endpoint_id)
        h = float(row[0] if health is None else max(0.0,min(1.0,float(health))))
        a = int(row[1] if active is None else max(0,int(active)))
        with self.db: self.db.execute("UPDATE endpoints SET state=?,health=?,active=?,updated_at=? WHERE endpoint_id=?", (st,h,a,time.time(),endpoint_id))

    def _rows(self) -> List[Endpoint]:
        rows=self.db.execute("SELECT endpoint_id,provider,account_scope,quota_group,state,health,concurrency,active,updated_at FROM endpoints").fetchall()
        return [Endpoint(r[0],r[1],r[2],r[3],CapacityState(r[4]),float(r[5]),int(r[6]),int(r[7]),float(r[8])) for r in rows]

    def select(self, count: int = 1, providers: Optional[Iterable[str]] = None) -> List[Endpoint]:
        allowed=set(providers or [])
        eps=[e for e in self._rows() if e.state==CapacityState.AVAILABLE and e.health>0 and e.active<e.concurrency and (not allowed or e.provider in allowed)]
        eps.sort(key=lambda e:(e.active/e.concurrency,-e.health,e.updated_at))
        # A quota group contributes only its explicitly reported free concurrency.
        # Multiple accounts are not silently treated as a quota-multiplication mechanism.
        out=[]; group_slots: Dict[str,int]={}
        for e in eps:
            used=group_slots.get(e.quota_group,0)
            free=max(0,e.concurrency-e.active)
            if used>=free: continue
            out.append(e); group_slots[e.quota_group]=used+1
            if len(out)>=max(0,int(count)): break
        return out

    def summary(self) -> Dict[str, object]:
        eps=self._rows(); states={s.value:0 for s in CapacityState}
        for e in eps: states[e.state.value]+=1
        return {"endpoints":len(eps),"states":states,"available_slots":sum(max(0,e.concurrency-e.active) for e in eps if e.state==CapacityState.AVAILABLE),"quota_bypass":False,"account_rotation_for_quota":False}

    def quota_group_for(self, endpoint_id: str) -> str:
        row=self.db.execute("SELECT quota_group FROM endpoints WHERE endpoint_id=?",(endpoint_id,)).fetchone()
        if not row: raise KeyError(endpoint_id)
        return str(row[0])

    def report_group(self, quota_group: str, state: CapacityState | str, health: Optional[float]=None) -> int:
        st=CapacityState(state).value; now=time.time()
        if health is None:
            with self.db:
                cur=self.db.execute("UPDATE endpoints SET state=?,updated_at=? WHERE quota_group=?",(st,now,quota_group))
        else:
            h=max(0.0,min(1.0,float(health)))
            with self.db:
                cur=self.db.execute("UPDATE endpoints SET state=?,health=?,updated_at=? WHERE quota_group=?",(st,h,now,quota_group))
        return int(cur.rowcount)

    def arm_single_probe(self, quota_group: str) -> Optional[str]:
        """After cooldown, expose only one endpoint in a shared quota group."""
        rows=self.db.execute("SELECT endpoint_id FROM endpoints WHERE quota_group=? ORDER BY health DESC,updated_at ASC",(quota_group,)).fetchall()
        if not rows: return None
        chosen=str(rows[0][0]); now=time.time()
        with self.db:
            self.db.execute("UPDATE endpoints SET state=?,active=0,updated_at=? WHERE quota_group=?",(CapacityState.QUOTA_WAIT.value,now,quota_group))
            self.db.execute("UPDATE endpoints SET state=?,active=0,updated_at=? WHERE endpoint_id=?",(CapacityState.AVAILABLE.value,now,chosen))
        return chosen

    def first_endpoint_for_provider(self, provider: str) -> Optional[str]:
        row=self.db.execute("SELECT endpoint_id FROM endpoints WHERE provider=? ORDER BY updated_at ASC LIMIT 1",(provider,)).fetchone()
        return str(row[0]) if row else None
