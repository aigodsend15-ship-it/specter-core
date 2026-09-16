from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from specter_federation_mesh import ProbeResult, SpecterFederationHub


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now
    def __call__(self) -> float:
        return self.now
    def advance(self, seconds: float) -> None:
        self.now += seconds


def sequence_http(results):
    queue = list(results)
    def probe(url: str, timeout_s: float) -> ProbeResult:
        if not queue:
            raise AssertionError(f"unexpected HTTP probe: {url}")
        return queue.pop(0)
    return probe


def fixed_tcp(result: ProbeResult):
    def probe(host: str, port: int, timeout_s: float) -> ProbeResult:
        return result
    return probe


class FederationMeshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fabric.sqlite3"
        self.clock = FakeClock()

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def hub(self, http_results, tcp_result, **kwargs):
        return SpecterFederationHub(
            db_path=self.db,
            peer_ttl_s=30,
            http_probe=sequence_http(http_results),
            tcp_probe=fixed_tcp(tcp_result),
            clock=self.clock,
            **kwargs,
        )

    def test_all_online_is_vivo(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        hub = self.hub([ok, ok], ok)
        result = hub.sync_federation()
        self.assertEqual(result["federation_status"], "VIVO")
        self.assertEqual(result["active_peer_count"], 3)
        self.assertTrue(all(p["status"] == "ACTIVE" for p in result["peers"]))

    def test_hf_offline_is_degraded(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        fail = ProbeResult(False, 3.0, error="timeout")
        hub = self.hub([ok, fail], ok)
        result = hub.sync_federation()
        self.assertEqual(result["federation_status"], "DEGRADED")
        self.assertEqual(result["active_peer_count"], 2)
        hf = next(p for p in result["peers"] if p["peer_id"] == "huggingface-vps-cluster")
        self.assertEqual(hf["status"], "OFFLINE")
        self.assertEqual(hf["last_error"], "timeout")

    def test_hermes_offline_is_persisted_offline(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        fail = ProbeResult(False, 1.0, error="connection refused")
        hub = self.hub([ok, ok], fail)
        result = hub.sync_federation()
        hermes = next(p for p in result["peers"] if p["peer_id"] == "hermes-desktop-k3")
        self.assertEqual(hermes["status"], "OFFLINE")
        self.assertEqual(result["federation_status"], "DEGRADED")

    def test_all_offline_is_offline(self):
        fail = ProbeResult(False, 1.0, error="down")
        hub = self.hub([fail, fail], fail)
        result = hub.sync_federation()
        self.assertEqual(result["federation_status"], "OFFLINE")
        self.assertEqual(result["active_peer_count"], 0)

    def test_active_transitions_to_offline_without_stale_active_row(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        hub = self.hub([ok, ok], ok)
        first = hub.sync_federation()
        self.assertEqual(first["active_peer_count"], 3)

        fail = ProbeResult(False, 1.0, error="down")
        hub2 = self.hub([fail, fail], fail)
        second = hub2.sync_federation()
        self.assertEqual(second["active_peer_count"], 0)
        self.assertTrue(all(p["status"] == "OFFLINE" for p in second["peers"]))

    def test_ttl_expires_explicit_registration_to_stale(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        hub = self.hub([ok, ok], ok)
        hub.register_peer("manual", "worker", "http://manual", ["test"])
        self.assertEqual(len(hub.get_active_peers()), 1)
        self.clock.advance(31)
        self.assertEqual(hub.get_active_peers(), [])
        peer = next(p for p in hub.get_peers() if p["peer_id"] == "manual")
        self.assertEqual(peer["status"], "STALE")

    def test_sqlite_wal_is_enabled(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        hub = self.hub([ok, ok], ok)
        self.assertEqual(hub.journal_mode(), "wal")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")

    def test_unverified_hardware_capabilities_not_claimed_by_default(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        hub = self.hub([ok, ok], ok)
        result = hub.sync_federation()
        hf = next(p for p in result["peers"] if p["peer_id"] == "huggingface-vps-cluster")
        self.assertNotIn("192_vcpu_sandbox", hf["capabilities"])
        self.assertNotIn("zerogpu_a10g", hf["capabilities"])
        self.assertIn("gradio_hub", hf["capabilities"])

    def test_declared_capabilities_are_preserved_explicitly(self):
        ok = ProbeResult(True, 1.0, status_code=200)
        hub = self.hub(
            [ok, ok],
            ok,
            declared_capabilities={"huggingface-vps-cluster": ["zerogpu_a10g"]},
        )
        result = hub.sync_federation()
        hf = next(p for p in result["peers"] if p["peer_id"] == "huggingface-vps-cluster")
        self.assertIn("zerogpu_a10g", hf["capabilities"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
