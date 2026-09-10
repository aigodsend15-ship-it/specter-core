import tempfile, unittest
from pathlib import Path
from federation_hub_v22 import FederationHub
from specter_mesh_protocol import build_envelope, verify_envelope

class FederationV22Tests(unittest.TestCase):
    def test_smp_hash_and_tamper(self):
        env=build_envelope("ARCHITECT","COORDINATOR","FINDING",{"x":1})
        self.assertTrue(verify_envelope(env))
        env["payload"]["x"]=2
        self.assertFalse(verify_envelope(env))

    def test_peer_message_meeting(self):
        with tempfile.TemporaryDirectory() as td:
            hub=FederationHub(Path(td)/"f.db")
            hub.register_peer("bitos","hub",["coordination","receipts"],endpoint="loopback")
            env=build_envelope("bitos","hermes","HELLO",{"protocol":"SMP/1"})
            first=hub.ingest(env); second=hub.ingest(env)
            self.assertFalse(first["deduplicated"]); self.assertTrue(second["deduplicated"])
            meeting=hub.open_meeting("federation bootstrap",{"bitos":"COORDINATOR"})
            self.assertEqual(meeting["status"],"OPEN")
            hub.close_meeting(meeting["meeting_id"])
            self.assertEqual(hub.summary()["messages"]["HELLO"],1)
            hub.close()

if __name__=="__main__": unittest.main()
