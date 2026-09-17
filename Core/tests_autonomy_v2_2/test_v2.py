import json, tempfile, unittest
from pathlib import Path
from autonomy_v2_2.context_lineage import ContextLineageManager
from autonomy_v2_2.capacity_manager import CapacityManager, CapacityState
from autonomy_v2_2.agent_fabric import DurableAgentFabric, DEFAULT_ROLES
from autonomy_v2_2.research_miner import PublicResearchMiner
from autonomy_v2_2.successor_chat import SuccessorChatManager
from autonomy_v2_2.autonomy_runtime import SovereignAutonomyRuntime

class V2Tests(unittest.TestCase):
    def test_lineage_branch_resume_merge(self):
        with tempfile.TemporaryDirectory() as td:
            m=ContextLineageManager(Path(td)/'l.db'); m.ensure_branch('p','main')
            a=m.create_checkpoint('p','main',{'objective':'x','next_action':'y'},event_seq=1)
            b=m.fork_branch('p','main',a['checkpoint_id'],'b1')
            c=m.create_checkpoint('p',b,{'objective':'x','next_action':'z'},event_seq=2)
            cap=json.loads(m.resume_capsule(c['checkpoint_id']))
            self.assertEqual(cap['branch_id'],'b1'); self.assertEqual(cap['event_seq'],2)
            merged=m.merge('p','main',[a['checkpoint_id'],c['checkpoint_id']],{'objective':'x','next_action':'done'},event_seq=3)
            self.assertTrue(merged['merge_id'].startswith('merge-')); m.close()

    def test_context_pressure_successor(self):
        with tempfile.TemporaryDirectory() as td:
            m=ContextLineageManager(Path(td)/'l.db'); s=SuccessorChatManager(m)
            self.assertEqual(s.classify(10,100,'VocÃª chegou Ã  duraÃ§Ã£o mÃ¡xima desta conversa'),'CONTEXT_EXHAUSTED')
            p=s.prepare_successor('p','main',{'objective':'x','next_action':'continue'},7,hard=True)
            self.assertIn('SPECTER_RESUME_V2',p['resume_prompt']); self.assertNotEqual(p['successor_branch'],'main'); m.close()

    def test_capacity_is_quota_aware(self):
        with tempfile.TemporaryDirectory() as td:
            c=CapacityManager(Path(td)/'c.db')
            c.register('a','chatgpt','profile-8','qg',1); c.register('b','chatgpt','profile-8-second-tab','qg',1)
            selected=c.select(10)
            self.assertEqual(len(selected),1)
            c.report('a',CapacityState.QUOTA_WAIT); self.assertFalse(c.summary()['quota_bypass']); c.close()

    def test_agent_fabric_lease_and_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            f=DurableAgentFabric(Path(td)/'f.db')
            t1=f.submit('ARCHITECT',{'x':1},dedup_key='same'); t2=f.submit('ARCHITECT',{'x':1},dedup_key='same')
            self.assertEqual(t1,t2); claim=f.claim('ARCHITECT','w1',30); self.assertEqual(claim['task_id'],t1)
            with self.assertRaises(RuntimeError): f.complete(t1,'w1',claim['lease_token']+1,{})
            f.complete(t1,'w1',claim['lease_token'],{'ok':True}); self.assertEqual(f.summary()['tasks']['DONE'],1); f.close()

    def test_research_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            r=PublicResearchMiner(Path(td)/'r.db')
            a=r.ingest('https://github.com/example/repo','body','repo'); b=r.ingest('https://github.com/example/repo','body2','repo')
            self.assertEqual(a,b); r.add_finding(a,'agents','typed registry','evidence',0.9)
            self.assertEqual(r.dedup_stats(),{'sources':1,'findings':1}); r.close()

    def test_runtime_seeds_ten_role_fabric(self):
        with tempfile.TemporaryDirectory() as td:
            r=SovereignAutonomyRuntime(td); out=r.seed_mission('build durable autonomy')
            self.assertEqual(len(out['tasks']),9); st=r.tick(); self.assertEqual(st['fabric']['roles'],10)
            self.assertFalse(st['invariants']['quota_bypass']); r.close()

if __name__=='__main__': unittest.main()
