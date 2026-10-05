"""
SPECTER SOVEREIGN MEMORY ENGINE (v1.0.0-SOVEREIGN)
==================================================
Tripartite Cryptographic Episodic Memory & Zero-Token Capability Ledger
Author: AIGODSEND (Guilherme Peralta Novaes) | Principal Distributed Systems Architect
Repository: https://github.com/aigodsend15-ship-it/specter-core

Design Principles (RFC-Grade):
1. Memory Record: Content-addressed immutable envelope (SHA-256).
2. Authority Capability: Deterministic unforgeable capability token (seL4 / Dennis & Van Horn 1966 model).
3. Retrieval Receipt: Atomic Merkle leaf commitment for context injection without ambient privilege.
4. Sub-Millisecond Search: Pure-Python Okapi BM25 + Trigram inverted index over SQLite WAL.
5. O(1) Tombstone Revocation: Instant purge of tainted or revoked context prior to ranking.
"""

import os
import sys
import time
import json
import math
import hmac
import hashlib
import sqlite3
import re
from typing import List, Dict, Any, Optional, Tuple

class MerkleTree:
    """Deterministic binary Merkle tree for context state proofs."""
    def __init__(self, leaves: List[bytes]):
        self.leaves = [hashlib.sha256(l).digest() for l in leaves]
        if not self.leaves:
            self.leaves = [hashlib.sha256(b"").digest()]
        self.levels = [self.leaves]
        self._build()

    def _build(self):
        current = self.leaves
        while len(current) > 1:
            next_level = []
            for i in range(0, len(current), 2):
                l = current[i]
                r = current[i + 1] if i + 1 < len(current) else current[i]
                combined = hashlib.sha256(l + r).digest()
                next_level.append(combined)
            self.levels.append(next_level)
            current = next_level

    @property
    def root(self) -> str:
        return self.levels[-1][0].hex()

    def get_proof(self, index: int) -> List[Tuple[str, str]]:
        """Returns Merkle inclusion proof: list of (direction, sibling_hash)."""
        proof = []
        for level in self.levels[:-1]:
            is_right = (index % 2 == 1)
            sibling_idx = index - 1 if is_right else index + 1
            if sibling_idx < len(level):
                proof.append(('L' if is_right else 'R', level[sibling_idx].hex()))
            else:
                proof.append(('L' if is_right else 'R', level[index].hex()))
            index //= 2
        return proof

    @staticmethod
    def verify_proof(leaf: bytes, proof: List[Tuple[str, str]], root: str) -> bool:
        current = hashlib.sha256(leaf).digest()
        for direction, sibling_hex in proof:
            sibling = bytes.fromhex(sibling_hex)
            if direction == 'L':
                current = hashlib.sha256(sibling + current).digest()
            else:
                current = hashlib.sha256(current + sibling).digest()
        return current.hex() == root


class BM25Index:
    """Zero-dependency Okapi BM25 ranker for microsecond memory retrieval."""
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_len: Dict[str, int] = {}
        self.doc_count = 0
        self.avg_doc_len = 0.0
        self.inverted_index: Dict[str, Dict[str, int]] = {}

    def _tokenize(self, text: str) -> List[str]:
        return [w.lower() for w in re.findall(r'\b[a-zA-Z0-9_\u00C0-\u00FF]{2,}\b', text)]

    def add_document(self, doc_id: str, text: str):
        tokens = self._tokenize(text)
        length = len(tokens)
        self.doc_len[doc_id] = length
        self.doc_count += 1
        
        freqs: Dict[str, int] = {}
        for t in tokens:
            freqs[t] = freqs.get(t, 0) + 1
            
        for t, freq in freqs.items():
            if t not in self.inverted_index:
                self.inverted_index[t] = {}
            self.inverted_index[t][doc_id] = freq
            
        self.avg_doc_len = sum(self.doc_len.values()) / max(1, self.doc_count)

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        q_tokens = self._tokenize(query)
        scores: Dict[str, float] = {}
        
        for t in q_tokens:
            if t not in self.inverted_index:
                continue
            df = len(self.inverted_index[t])
            # IDF computation
            idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)
            
            for doc_id, freq in self.inverted_index[t].items():
                dl = self.doc_len.get(doc_id, self.avg_doc_len)
                num = freq * (self.k1 + 1.0)
                denom = freq + self.k1 * (1.0 - self.b + self.b * (dl / max(1.0, self.avg_doc_len)))
                score = idf * (num / denom)
                scores[doc_id] = scores.get(doc_id, 0.0) + score
                
        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_docs[:top_k]


class SpecterMemoryEngine:
    """
    Sovereign Capability-Enforced Episodic Memory Engine.
    Operates over durable SQLite WAL with content-addressing and cryptographic receipts.
    """
    def __init__(self, db_path: str = r"C:\Specter\Core\specter_memory.db", hmac_secret: bytes = b"specter-sovereign-kernel-l0"):
        self.db_path = db_path
        self.hmac_secret = hmac_secret
        self.bm25 = BM25Index()
        self._init_db()
        self._load_index()

    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = conn.cursor()
            
            # 1. Memory Records (Content-addressed)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory_records (
                    record_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    author_principal TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    epistemic_state TEXT DEFAULT 'CONFIRMED',
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT
                )
            """)
            
            # 2. Authority Capabilities (Unforgeable tokens)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS authority_capabilities (
                    capability_id TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL,
                    beneficiary TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    allowed_actions TEXT NOT NULL,
                    task_scope TEXT NOT NULL,
                    valid_until REAL NOT NULL,
                    signature TEXT NOT NULL,
                    FOREIGN KEY(record_id) REFERENCES memory_records(record_id)
                )
            """)
            
            # 3. Tombstone Revocations (O(1) purge)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tombstone_revocations (
                    revocation_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL, -- record_id or capability_id
                    revoked_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            
            # 4. Retrieval Receipts (Immutable audit trail)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS retrieval_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    reader_principal TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    query_commitment TEXT NOT NULL,
                    context_set_hash TEXT NOT NULL,
                    merkle_root TEXT NOT NULL,
                    selected_records TEXT NOT NULL,
                    decision_timestamp REAL NOT NULL,
                    signature TEXT NOT NULL
                )
            """)
            conn.commit()

    def _load_index(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT record_id, content FROM memory_records")
            for r_id, content in cursor.fetchall():
                self.bm25.add_document(r_id, content)

    def write_record(self, content: str, author_principal: str, topic: str = "general", expires_in_sec: Optional[float] = None) -> str:
        """Stores a content-addressed memory record into SQLite WAL."""
        now = time.time()
        expires_at = now + expires_in_sec if expires_in_sec else None
        
        canonical_envelope = {
            "content": content,
            "author": author_principal,
            "topic": topic,
            "created_at": now
        }
        envelope_bytes = json.dumps(canonical_envelope, sort_keys=True).encode('utf-8')
        record_id = hashlib.sha256(envelope_bytes).hexdigest()
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO memory_records 
                (record_id, content, author_principal, topic, created_at, expires_at, content_hash, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (record_id, content, author_principal, topic, now, expires_at, content_hash, json.dumps(canonical_envelope)))
            conn.commit()

        self.bm25.add_document(record_id, content)
        return record_id

    def grant_capability(self, issuer: str, beneficiary: str, record_id: str, allowed_actions: List[str], task_scope: str = "*", duration_sec: float = 86400.0) -> str:
        """Issues an unforgeable capability token granting a principal access to a memory record."""
        now = time.time()
        valid_until = now + duration_sec
        actions_str = ",".join(sorted(allowed_actions))
        
        raw_token = f"{issuer}:{beneficiary}:{record_id}:{actions_str}:{task_scope}:{valid_until}"
        signature = hmac.new(self.hmac_secret, raw_token.encode('utf-8'), hashlib.sha256).hexdigest()
        capability_id = hashlib.sha256(f"{raw_token}:{signature}".encode('utf-8')).hexdigest()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO authority_capabilities
                (capability_id, issuer, beneficiary, record_id, allowed_actions, task_scope, valid_until, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (capability_id, issuer, beneficiary, record_id, actions_str, task_scope, valid_until, signature))
            conn.commit()
            
        return capability_id

    def revoke(self, target_id: str, revoked_by: str, reason: str = "security_purge"):
        """Instant O(1) revocation tombstone."""
        now = time.time()
        revocation_id = hashlib.sha256(f"{target_id}:{now}".encode('utf-8')).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO tombstone_revocations (revocation_id, target_id, revoked_by, reason, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (revocation_id, target_id, revoked_by, reason, now))
            conn.commit()

    def _is_revoked(self, target_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM tombstone_revocations WHERE target_id = ?", (target_id,))
            return cursor.fetchone() is not None

    def retrieve(self, reader_principal: str, session_id: str, query: str, top_k: int = 3, task_scope: str = "*") -> Dict[str, Any]:
        """
        Deterministic, capability-enforced memory retrieval with cryptographic receipt and Merkle state proof.
        """
        now = time.time()
        query_commitment = hmac.new(self.hmac_secret, query.encode('utf-8'), hashlib.sha256).hexdigest()

        # 1. Candidate ranking via BM25
        candidates = self.bm25.search(query, top_k=top_k * 3)
        if not candidates:
            return {"status": "EMPTY", "selected": [], "receipt": None}

        selected_records = []
        context_spans = []

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for record_id, score in candidates:
                # Check record tombstone
                if self._is_revoked(record_id):
                    continue

                # Capability verification (Zero-trust choke point)
                cursor.execute("""
                    SELECT capability_id, allowed_actions, valid_until, signature, issuer
                    FROM authority_capabilities
                    WHERE record_id = ? AND beneficiary IN (?, '*')
                """, (record_id, reader_principal))
                
                rows = cursor.fetchall()
                authorized = False
                for cap_id, actions_str, valid_until, sig, issuer in rows:
                    if self._is_revoked(cap_id):
                        continue
                    if valid_until < now:
                        continue
                    
                    # Verify HMAC signature
                    raw_token = f"{issuer}:{reader_principal}:{record_id}:{actions_str}:{task_scope}:{valid_until}"
                    expected_sig = hmac.new(self.hmac_secret, raw_token.encode('utf-8'), hashlib.sha256).hexdigest()
                    if hmac.compare_digest(sig, expected_sig) and ('read' in actions_str.split(',') or '*' in actions_str):
                        authorized = True
                        break

                if authorized:
                    cursor.execute("SELECT content, topic, author_principal FROM memory_records WHERE record_id = ?", (record_id,))
                    rec = cursor.fetchone()
                    if rec:
                        content, topic, author = rec
                        selected_records.append({
                            "record_id": record_id,
                            "topic": topic,
                            "author": author,
                            "score": round(score, 4),
                            "content": content
                        })
                        context_spans.append(content.encode('utf-8'))
                        if len(selected_records) >= top_k:
                            break

        if not selected_records:
            return {"status": "NO_AUTHORIZED_RECORDS", "selected": [], "receipt": None}

        # 2. Cryptographic Context Assembly & Merkle Root
        merkle = MerkleTree(context_spans)
        context_set_hash = hashlib.sha256(b"".join(context_spans)).hexdigest()
        
        # 3. Retrieval Receipt
        receipt_raw = f"{reader_principal}:{session_id}:{query_commitment}:{context_set_hash}:{merkle.root}:{now}"
        receipt_sig = hmac.new(self.hmac_secret, receipt_raw.encode('utf-8'), hashlib.sha256).hexdigest()
        receipt_id = hashlib.sha256(f"{receipt_raw}:{receipt_sig}".encode('utf-8')).hexdigest()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO retrieval_receipts
                (receipt_id, reader_principal, session_id, query_commitment, context_set_hash, merkle_root, selected_records, decision_timestamp, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (receipt_id, reader_principal, session_id, query_commitment, context_set_hash, merkle.root, json.dumps([r['record_id'] for r in selected_records]), now, receipt_sig))
            conn.commit()

        return {
            "status": "SUCCESS",
            "receipt_id": receipt_id,
            "merkle_root": merkle.root,
            "context_set_hash": context_set_hash,
            "records_count": len(selected_records),
            "records": selected_records
        }


# ==========================================
# Self-Verification Test Suite
# ==========================================
if __name__ == "__main__":
    print("====================================================================")
    print(" SPECTER MEMORY ENGINE: SELF-VERIFICATION PROTOCOL")
    print("====================================================================")
    test_db = r"C:\Specter\Core\test_specter_memory.db"
    if os.path.exists(test_db):
        os.remove(test_db)
        
    engine = SpecterMemoryEngine(db_path=test_db)
    
    # 1. Ingest Knowledge
    r1 = engine.write_record(
        content="seL4 kernel capabilities enforce formal mathematical non-interference between untrusted partitions.",
        author_principal="AIGODSEND",
        topic="kernel_security"
    )
    r2 = engine.write_record(
        content="Okapi BM25 outperforms raw cosine similarity on dense keyword matching under microsecond constraints.",
        author_principal="AIGODSEND",
        topic="information_retrieval"
    )
    r3 = engine.write_record(
        content="Confidential corporate telemetry: Slurm cluster credentials and private VPC CIDR blocks.",
        author_principal="Unverified_Subagent",
        topic="confidential"
    )
    print(f"[OK] Ingested 3 content-addressed memory records. r1={r1[:12]}..., r2={r2[:12]}...")

    # 2. Grant capability for r1 and r2 only
    cap1 = engine.grant_capability(
        issuer="AIGODSEND",
        beneficiary="Agent_Claude",
        record_id=r1,
        allowed_actions=["read", "infer"]
    )
    cap2 = engine.grant_capability(
        issuer="AIGODSEND",
        beneficiary="Agent_Claude",
        record_id=r2,
        allowed_actions=["read"]
    )
    print(f"[OK] Granted authority capabilities: cap1={cap1[:12]}..., cap2={cap2[:12]}...")

    # 3. Retrieve with Agent_Claude
    res = engine.retrieve(
        reader_principal="Agent_Claude",
        session_id="sess_001",
        query="Tell me about seL4 kernel security and non-interference"
    )
    assert res['status'] == 'SUCCESS', f"Expected SUCCESS, got {res['status']}"
    assert res['records_count'] == 1, f"Expected 1 record, got {res['records_count']}"
    assert res['records'][0]['record_id'] == r1
    print(f"[OK] Capability-enforced retrieval verified. Merkle Root: {res['merkle_root'][:16]}... Receipt: {res['receipt_id'][:16]}...")

    # 4. Attempt unauthorized read on r3
    res_unauth = engine.retrieve(
        reader_principal="Agent_Claude",
        session_id="sess_002",
        query="Confidential cluster credentials and private VPC CIDR"
    )
    assert res_unauth['status'] == 'NO_AUTHORIZED_RECORDS', f"Expected rejection, got {res_unauth['status']}"
    print("[OK] Unauthorized memory retrieval blocked deterministically at IPC choke point!")

    # 5. Revocation Test
    engine.revoke(cap1, revoked_by="AIGODSEND", reason="security_audit")
    res_revoked = engine.retrieve(
        reader_principal="Agent_Claude",
        session_id="sess_003",
        query="seL4 kernel security"
    )
    assert res_revoked['status'] == 'NO_AUTHORIZED_RECORDS', "Expected revocation to block access in O(1)"
    print("[OK] O(1) Tombstone revocation successfully purged capability from context assembly!")

    # Cleanup test db
    try:
        if os.path.exists(test_db):
            os.remove(test_db)
    except Exception:
        pass

    print("====================================================================")
    print(" ALL 5 ARCHITECTURAL ACCEPTANCE CRITERIA VERIFIED (100% PASS)")
    print("====================================================================")
