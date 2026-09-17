import tempfile, time, unittest
from pathlib import Path
from autonomy_v2_2.capacity_manager import CapacityManager, CapacityState
from autonomy_v2_2.incident_guard import ProviderIncidentGuard
from autonomy_v2_2.deliberation import DeliberationEngine
from autonomy_v2_2.autonomy_runtime import SovereignAutonomyRuntime

class V21Tests(unittest.TestCase):
    def test_rate_limit_text_is_detected(self):
        self.assertEqual(ProviderIncidentGuard.classify_text(
            "Too many requests. We've temporarily limited access."), "RATE_LIMIT")
        self.assertIsNone(ProviderIncidentGuard.classify_text("normal response"))

    def test_backoff_grows(self):
        with tempfile.TemporaryDirectory() as td:
            g=ProviderIncidentGuard(Path(td)/"i.db",base_backoff_s=30,max_backoff_s=300)
            a=g.record("q","Too many requests")
            b=g.record("q","Too many requests")
            self.assertEqual(a.strikes,1); self.assertEqual(b.strikes,2)
            self.assertGreater(b.blocked_until,a.blocked_until)
            g.close()

    def test_shared_quota_group_arms_one_probe(self):
        with tempfile.TemporaryDirectory() as td:
            r=SovereignAutonomyRuntime(td)
            r.register_endpoint("a","chatgpt","profile8","chatgpt:profile8",1)
            r.register_endpoint("b","chatgpt","profile8-tab2","chatgpt:profile8",1)
            out=r.report_provider_text("a","Too many requests. Please wait a few minutes.",retry_after_s=30)
            self.assertEqual(out["incident"],"RATE_LIMIT")
            self.assertEqual(r.capacity.summary()["available_slots"],0)
            with r.incidents.db:
                r.incidents.db.execute("UPDATE quota_incidents SET blocked_until=? WHERE quota_group=?",(time.time()-1,"chatgpt:profile8"))
            st=r.tick()
            self.assertEqual(st["capacity"]["available_slots"],1)
            available=r.capacity.select(10)
            self.assertEqual(len(available),1)
            self.assertEqual(available[0].quota_group,"chatgpt:profile8")
            r.close()

    def test_deliberation_requires_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            d=DeliberationEngine(Path(td)/"d.db")
            d.add("x","ARCHITECT","use typed events",["repo:A"],0.8,"PROPOSED")
            self.assertFalse(d.synthesis_gate("x")["promote"])
            d.add("x","VERIFIER","tests reproduce claim",["test:1"],0.9,"APPROVED")
            self.assertTrue(d.synthesis_gate("x")["promote"])
            d.close()

    def test_security_reject_blocks_promotion(self):
        with tempfile.TemporaryDirectory() as td:
            d=DeliberationEngine(Path(td)/"d.db")
            d.add("x","ARCHITECT","proposal",["e1"],0.8,"PROPOSED")
            d.add("x","VERIFIER","verified",["e2"],0.9,"APPROVED")
            d.add("x","SECURITY","unsafe edge case",["e3"],0.95,"REJECTED")
            gate=d.synthesis_gate("x")
            self.assertFalse(gate["promote"])
            self.assertTrue(gate["security_reject"])
            d.close()

if __name__=='__main__': unittest.main()
