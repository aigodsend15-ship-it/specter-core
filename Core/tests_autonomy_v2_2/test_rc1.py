import concurrent.futures, copy, sqlite3, tempfile, unittest
from pathlib import Path
from autonomy_v2_2.agent_fabric import DurableAgentFabric
from autonomy_v2_2.federation_hub import FederationHub
from autonomy_v2_2.governance import GovernanceGate
from autonomy_v2_2.action_router import GovernedActionRouter
from autonomy_v2_2.mesh_protocol import make_envelope, envelope_digest
from autonomy_v2_2.web_agent_worker import WebAgentWorker, WebDispatchStore

class RC1ReleaseGates(unittest.TestCase):
    def test_50_concurrent_claims_exactly_one_owner(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'fabric.db'; seed=DurableAgentFabric(db)
            tid=seed.submit('ARCHITECT',{'objective':'race'},dedup_key='race'); seed.close()
            workers=[DurableAgentFabric(db) for _ in range(50)]
            def claim(i): return workers[i].claim('ARCHITECT',f'w{i}',30)
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex: results=list(ex.map(claim,range(50)))
            wins=[r for r in results if r]
            self.assertEqual(len(wins),1); self.assertEqual(wins[0]['task_id'],tid)
            check=workers[0]; n=check.db.execute("SELECT COUNT(*) FROM task_events WHERE task_id=? AND kind='claimed'",(tid,)).fetchone()[0]
            self.assertEqual(n,1)
            for w in workers: w.close()

    def test_stale_fencing_cannot_complete_after_reclaim(self):
        with tempfile.TemporaryDirectory() as td:
            f=DurableAgentFabric(Path(td)/'f.db'); tid=f.submit('ARCHITECT',{'x':1})
            a=f.claim('ARCHITECT','old',30)
            with f.lock: f.db.execute("UPDATE tasks SET lease_until=0 WHERE task_id=?",(tid,)); f.db.commit()
            f.reconcile_expired(); b=f.claim('ARCHITECT','new',30)
            self.assertGreater(b['lease_token'],a['lease_token'])
            with self.assertRaises(RuntimeError): f.complete(tid,'old',a['lease_token'],{'bad':1})
            f.complete(tid,'new',b['lease_token'],{'ok':1}); f.close()
    def test_governance_binds_operation_args_scope_policy_and_receipt(self):
        gate=GovernanceGate(b'G'*32); args={'path':'C:/safe/file.txt'}
        auth=gate.issue('op-1','files.hash',args,'workspace:C:/safe','policy:v1')
        calls=[]
        router=GovernedActionRouter(gate,lambda cap,a,scope,op: calls.append((cap,a,scope,op)) or {'sha256':'abc'})
        out=router.execute({'operation_id':'op-1','capability':'files.hash','args':args,'scope':'workspace:C:/safe','policy_hash':'policy:v1'},auth)
        self.assertEqual(len(calls),1)
        self.assertTrue(gate.verify_receipt(out['receipt'],auth,'op-1','files.hash',args,'workspace:C:/safe','policy:v1'))
        with self.assertRaises(PermissionError):
            router.execute({'operation_id':'op-1','capability':'files.hash','args':{'path':'C:/other'},'scope':'workspace:C:/safe','policy_hash':'policy:v1'},auth)
        tampered=copy.deepcopy(out['receipt']); tampered['result']={'sha256':'evil'}
        self.assertFalse(gate.verify_receipt(tampered,auth,'op-1','files.hash',args,'workspace:C:/safe','policy:v1'))

    def _enroll(self,h,node):
        token=h.create_enrollment(node); return h.enroll(token,node,['mesh.message'])

    def test_smp_broadcast_ack_is_per_recipient_and_replay_divergence_fails(self):
        with tempfile.TemporaryDirectory() as td:
            h=FederationHub(Path(td)/'hub.db')
            for n in ('sender','peer-a','peer-b'): self._enroll(h,n)
            env=make_envelope('sender','*','FINDING',{'claim':'x'})
            first=h.ingest_message('sender',env,['peer-a','peer-b']); self.assertFalse(first['deduplicated'])
            self.assertEqual(len(h.pending_messages('peer-a')),1); self.assertEqual(len(h.pending_messages('peer-b')),1)
            self.assertTrue(h.ack_message('peer-a',env['message_id']))
            self.assertEqual(len(h.pending_messages('peer-a')),0); self.assertEqual(len(h.pending_messages('peer-b')),1)
            again=h.ingest_message('sender',env,['peer-a','peer-b']); self.assertTrue(again['deduplicated'])
            divergent=copy.deepcopy(env); divergent['body']={'claim':'different'}; divergent['content_sha256']=envelope_digest(divergent)
            with self.assertRaises(RuntimeError): h.ingest_message('sender',divergent,['peer-a','peer-b'])
            with self.assertRaises(PermissionError): h.ingest_message('peer-a',env,['peer-a'])
            h.close()
    def test_web_restart_reconciles_before_any_resend(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'dispatch.db'; calls=[]; payload={'objective':'x','instruction':'review','checkpoint_id':'cp1'}
            s=WebDispatchStore(db); w=WebAgentWorker(s)
            one=w.dispatch_once('t1','ARCHITECT',payload,'e1','conv1',lambda c,p: calls.append(p) or True,'AVAILABLE')
            self.assertEqual(one['state'],'SUBMITTED'); self.assertEqual(len(calls),1); s.close()
            s2=WebDispatchStore(db); w2=WebAgentWorker(s2)
            self.assertEqual(len(s2.recover_pending()),1)
            rec=w2.reconcile('t1',lambda c,h:{'user_turn_present':True,'streaming':False})
            self.assertEqual(rec['state'],'WAITING_MODEL')
            two=w2.dispatch_once('t1','ARCHITECT',payload,'e1','conv1',lambda c,p: calls.append(p) or True,'AVAILABLE')
            self.assertEqual(two['state'],'WAITING_MODEL'); self.assertEqual(len(calls),1)
            done=w2.reconcile('t1',lambda c,h:{'response_text':'SXL/1 FACT {"claim":"ok"}'})
            self.assertEqual(done['state'],'COMPLETE'); self.assertEqual(len(calls),1); s2.close()

    def test_old_federation_db_migrates_non_destructively(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'old.db'; db=sqlite3.connect(path)
            db.execute("CREATE TABLE legacy_marker(k TEXT PRIMARY KEY,v TEXT)"); db.execute("INSERT INTO legacy_marker VALUES('keep','yes')"); db.commit(); db.close()
            h=FederationHub(path)
            with h._connect() as check:
                self.assertEqual(check.execute("SELECT v FROM legacy_marker WHERE k='keep'").fetchone()[0],'yes')
                tables={r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn('messages',tables); self.assertIn('deliveries',tables); h.close()

if __name__=='__main__': unittest.main()
