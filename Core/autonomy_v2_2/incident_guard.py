from __future__ import annotations
import sqlite3, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

RATE_LIMIT_PATTERNS = (
    "too many requests",
    "temporarily limited",
    "please wait a few minutes",
    "rate limit",
    "rate-limited",
    "http 429",
    "status 429",
)

@dataclass(frozen=True)
class QuotaIncident:
    quota_group: str
    strikes: int
    blocked_until: float
    reason: str
    updated_at: float

class ProviderIncidentGuard:
    """Persistent circuit-breaker for provider throttling; no quota bypass."""
    def __init__(self, path: str | Path, base_backoff_s: int = 120, max_backoff_s: int = 3600):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.base = max(30, int(base_backoff_s)); self.maximum = max(self.base, int(max_backoff_s))
        self.db = sqlite3.connect(str(self.path), timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS quota_incidents(
            quota_group TEXT PRIMARY KEY,
            strikes INTEGER NOT NULL,
            blocked_until REAL NOT NULL,
            reason TEXT NOT NULL,
            updated_at REAL NOT NULL
        )""")
        self.db.commit()

    def close(self): self.db.close()

    @staticmethod
    def classify_text(text: str) -> Optional[str]:
        low = (text or "").lower()
        for p in RATE_LIMIT_PATTERNS:
            if p in low: return "RATE_LIMIT"
        return None

    def record(self, quota_group: str, reason: str, retry_after_s: Optional[int] = None) -> QuotaIncident:
        now = time.time()
        row = self.db.execute("SELECT strikes FROM quota_incidents WHERE quota_group=?", (quota_group,)).fetchone()
        strikes = (int(row[0]) if row else 0) + 1
        delay = int(retry_after_s) if retry_after_s is not None else min(self.maximum, self.base * (2 ** (strikes - 1)))
        blocked = now + max(1, delay)
        with self.db:
            self.db.execute("""INSERT INTO quota_incidents VALUES(?,?,?,?,?)
                ON CONFLICT(quota_group) DO UPDATE SET strikes=excluded.strikes,
                blocked_until=excluded.blocked_until,reason=excluded.reason,updated_at=excluded.updated_at""",
                (quota_group, strikes, blocked, reason[:1000], now))
        return QuotaIncident(quota_group, strikes, blocked, reason[:1000], now)

    def clear(self, quota_group: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM quota_incidents WHERE quota_group=?", (quota_group,))

    def get(self, quota_group: str) -> Optional[QuotaIncident]:
        row = self.db.execute("SELECT quota_group,strikes,blocked_until,reason,updated_at FROM quota_incidents WHERE quota_group=?", (quota_group,)).fetchone()
        return QuotaIncident(*row) if row else None

    def blocked(self, quota_group: str, now: Optional[float] = None) -> bool:
        inc = self.get(quota_group)
        return bool(inc and inc.blocked_until > (time.time() if now is None else now))

    def remaining_s(self, quota_group: str) -> int:
        inc = self.get(quota_group)
        if not inc: return 0
        return max(0, int(round(inc.blocked_until - time.time())))

    def due_groups(self):
        now = time.time()
        return [r[0] for r in self.db.execute("SELECT quota_group FROM quota_incidents WHERE blocked_until<=? ORDER BY updated_at ASC", (now,)).fetchall()]

    def summary(self):
        now = time.time()
        rows = self.db.execute("SELECT quota_group,strikes,blocked_until,reason,updated_at FROM quota_incidents ORDER BY updated_at DESC").fetchall()
        return [{"quota_group":r[0],"strikes":int(r[1]),"blocked":float(r[2])>now,"retry_in_s":max(0,int(float(r[2])-now)),"reason":r[3],"updated_at":float(r[4])} for r in rows]
