"""Evidence-based local diagnostic workers. Does not impersonate LLM agents."""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, subprocess, sys, time
from pathlib import Path
from specter_federation_diagnostics import SpecterFederationHub, DB_PATH
ROOT = Path(__file__).resolve().parent
TEST_MODULES = ['test_unified_inference_gateway', 'test_mesh_peer_client']

class SpecterAgentSwarm:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS swarm_dialogue_ledger (turn_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, sender TEXT NOT NULL, receiver TEXT NOT NULL, dsl_message TEXT NOT NULL, hash_digest TEXT NOT NULL)')
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')
        return conn
    def exchange_message(self, sender, receiver, dsl):
        with self._get_conn() as conn:
            return conn.execute('INSERT INTO swarm_dialogue_ledger(timestamp,sender,receiver,dsl_message,hash_digest) VALUES (?,?,?,?,?)', (time.time(), sender, receiver, dsl, hashlib.sha256(dsl.encode()).hexdigest())).lastrowid
    def run_conversation_cycle(self, run_tests=False):
        health = SpecterFederationHub(self.db_path).sync_federation()
        verification = {'status': 'NOT_RUN'}
        if run_tests:
            try:
                result = subprocess.run([sys.executable, '-m', 'unittest', *TEST_MODULES], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
                output = result.stdout + result.stderr
                logs = self.db_path.parent / 'swarm-test-logs'; logs.mkdir(exist_ok=True)
                logfile = logs / (str(time.time_ns())+'.txt'); logfile.write_text(output, encoding='utf-8')
                verification = {'status':'PASSED' if result.returncode==0 else 'FAILED', 'exit_code':result.returncode, 'sha256':hashlib.sha256(output.encode()).hexdigest(), 'log':str(logfile)}
            except subprocess.TimeoutExpired:
                verification = {'status':'TIMEOUT'}
        payloads = [('Specter_Sentinel', 'Specter_Synthesizer', {'reachability':health['federation_status'], 'reachable_services':health['active_peer_count']}),
                    ('Specter_Synthesizer','Hermes_Bridge_Worker',verification),
                    ('Hermes_Bridge_Worker','Specter_Sentinel',{'cloud_api_reachable':health['measurements']['huggingface-space']['ok'], 'task_sync':'NOT_PERFORMED', 'llm_dialogue':'NOT_CONFIGURED'})]
        exchanges=[]
        for sender,receiver,evidence in payloads:
            dsl=':VERIFY '+json.dumps(evidence,ensure_ascii=True)+'\n:MEM Local diagnostic record; no remote task completion claimed.'
            turn=self.exchange_message(sender,receiver,dsl)
            exchanges.append({'turn':turn,'from':sender,'to':receiver,'evidence':evidence,'dsl':dsl})
        return exchanges

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop',action='store_true')
    parser.add_argument('--interval',type=float,default=300)
    parser.add_argument('--run-tests',action='store_true')
    parser.add_argument('--db',type=Path)
    args=parser.parse_args()
    if args.interval < 30: parser.error('--interval must be at least 30 seconds')
    swarm=SpecterAgentSwarm(args.db)
    try:
        while True:
            print(json.dumps(swarm.run_conversation_cycle(args.run_tests),ensure_ascii=True),flush=True)
            if not args.loop: break
            time.sleep(args.interval)
    except KeyboardInterrupt: pass
if __name__=='__main__': main()
