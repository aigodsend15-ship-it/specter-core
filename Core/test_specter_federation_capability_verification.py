from __future__ import annotations

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


class CapabilityVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fabric.sqlite3"
        self.clock = FakeClock()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def hub(self, *, hf_healthy: bool = True) -> SpecterFederationHub:
        http_results = iter(
            [
                ProbeResult(True, 1.0, status_code=200),
                ProbeResult(hf_healthy, 2.0, status_code=200 if hf_healthy else None, error=None if hf_healthy else "down"),
            ]
        )

        def http_probe(url: str, timeout_s: float) -> ProbeResult:
            return next(http_results)

        def tcp_probe(host: str, port: int, timeout_s: float) -> ProbeResult:
            return ProbeResult(True, 1.5, evidence={"transport": "tcp"})

        return SpecterFederationHub(
            db_path=self.db,
            peer_ttl_s=30,
            http_probe=http_probe,
            tcp_probe=tcp_probe,
            clock=self.clock,
            declared_capabilities={"huggingface-vps-cluster": ["zerogpu_a10g"]},
        )

    def test_declared_capability_is_not_verified_or_selected_by_default(self) -> None:
        hub = self.hub()
        result = hub.sync_federation()
        hf = next(p for p in result["peers"] if p["peer_id"] == "huggingface-vps-cluster")

        self.assertIn("zerogpu_a10g", hf["capabilities"])
        self.assertIn("zerogpu_a10g", hf["declared_capabilities"])
        self.assertNotIn("zerogpu_a10g", hf["verified_capabilities"])
        self.assertEqual(hub.select_active_peers(["zerogpu_a10g"]), [])
        self.assertEqual(
            [p["peer_id"] for p in hub.select_active_peers(["zerogpu_a10g"], allow_declared=True)],
            ["huggingface-vps-cluster"],
        )

    def test_generic_hf_health_does_not_verify_gradio_hub(self) -> None:
        hub = self.hub()
        result = hub.sync_federation()
        hf = next(p for p in result["peers"] if p["peer_id"] == "huggingface-vps-cluster")

        self.assertIn("gradio_hub", hf["capabilities"])
        self.assertNotIn("gradio_hub", hf["verified_capabilities"])

    def test_transport_and_local_storage_capabilities_are_verified(self) -> None:
        hub = self.hub()
        result = hub.sync_federation()
        peers = {p["peer_id"]: p for p in result["peers"]}

        self.assertEqual(
            peers["specter-local-core"]["verified_capabilities"],
            ["http_health", "sqlite_wal"],
        )
        self.assertEqual(
            peers["hermes-desktop-k3"]["verified_capabilities"],
            ["tcp_endpoint"],
        )
        self.assertEqual(
            peers["huggingface-vps-cluster"]["verified_capabilities"],
            ["http_health"],
        )

    def test_failed_probe_clears_current_verified_capabilities(self) -> None:
        hub = self.hub(hf_healthy=False)
        result = hub.sync_federation()
        hf = next(p for p in result["peers"] if p["peer_id"] == "huggingface-vps-cluster")

        self.assertEqual(hf["status"], "OFFLINE")
        self.assertEqual(hf["verified_capabilities"], [])
        self.assertNotIn(hf, hub.select_active_peers(["http_health"]))

    def test_explicit_registration_is_not_capability_verification(self) -> None:
        hub = self.hub()
        hub.register_peer("manual", "worker", "http://manual", ["custom_accelerator"])
        peer = next(p for p in hub.get_peers() if p["peer_id"] == "manual")

        self.assertEqual(peer["verified_capabilities"], [])
        self.assertEqual(peer["declared_capabilities"], ["custom_accelerator"])
        self.assertEqual(hub.select_active_peers(["custom_accelerator"]), [])
        self.assertEqual(
            [p["peer_id"] for p in hub.select_active_peers(["custom_accelerator"], allow_declared=True)],
            ["manual"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
