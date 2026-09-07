# -*- coding: utf-8 -*-
"""
================================================================================
UNITTESTS: SPECTER 24/7 RESILIENT SUPERVISOR (specter_supervisor_247.py)
================================================================================
Valida:
1. Sondas de porta TCP e métricas de RAM.
2. Contagem e inspeção de tarefas SQLite WAL.
3. Ciclo de vida e arquivo de controle (RUN, PAUSE, STOP).
4. Gravação atômica e leitura de supervisor_health.json.
5. Tolerância a falhas e auto-recuperação do processo secundário Gateway.
6. Execução ponta a ponta de ciclo único (--single-cycle) com exit code 0.
================================================================================
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Importar o módulo supervisor
import specter_supervisor_247 as sup


class TestSpecterSupervisor247(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.test_dir.name)

    def tearDown(self):
        try:
            self.test_dir.cleanup()
        except Exception:
            pass

    def test_tcp_port_probe(self):
        """Verifica precisão da detecção de portas online e offline."""
        # Porta fechada/aleatória
        closed_port = 59123
        self.assertFalse(sup.check_port_online(closed_port, timeout=0.1))

        # Abrir porta temporária local
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        open_port = sock.getsockname()[1]

        try:
            self.assertTrue(sup.check_port_online(open_port, timeout=0.2))
        finally:
            sock.close()

    def test_ram_usage_calculation(self):
        """Verifica que o consumo de RAM é reportado como valor float positivo razoável."""
        ram = sup.get_ram_usage_mb()
        self.assertIsInstance(ram, float)
        self.assertGreater(ram, 0.0)
        self.assertLess(ram, 1024.0)  # Deve ser bem menor que 1GB

    def test_sqlite_wal_task_counting(self):
        """Verifica a contagem correta de tarefas ativas, pendentes e totais."""
        test_db = self.tmp_path / "test_fabric.sqlite3"
        with sqlite3.connect(test_db) as con:
            con.execute("""
                CREATE TABLE broker_tasks (
                    id TEXT PRIMARY KEY,
                    idem TEXT UNIQUE,
                    state TEXT NOT NULL
                )
            """)
            con.execute("INSERT INTO broker_tasks VALUES ('1', 'idem1', 'INIT')")
            con.execute("INSERT INTO broker_tasks VALUES ('2', 'idem2', 'PLAN')")
            con.execute("INSERT INTO broker_tasks VALUES ('3', 'idem3', 'EXEC')")
            con.execute("INSERT INTO broker_tasks VALUES ('4', 'idem4', 'ATTAINED')")
            con.execute("INSERT INTO broker_tasks VALUES ('5', 'idem5', 'ESCALATE')")

        counts = sup.get_db_task_counts(db_path=test_db)
        self.assertEqual(counts["total"], 5)
        self.assertEqual(counts["pending"], 3)  # INIT, PLAN, EXEC
        self.assertEqual(counts["active"], 2)   # PLAN, EXEC

    def test_control_file_lifecycle(self):
        """Verifica gravação e leitura de comandos de controle (RUN, PAUSE, STOP)."""
        ctrl = self.tmp_path / "control.json"
        orig_ctrl = sup.CONTROL_FILE
        try:
            sup.CONTROL_FILE = ctrl
            # Arquivo inexistente retorna RUN
            self.assertEqual(sup.read_control_command(), "RUN")

            # Escrever PAUSE
            ctrl.write_text(json.dumps({"command": "PAUSE"}), encoding="utf-8")
            self.assertEqual(sup.read_control_command(), "PAUSE")

            # Escrever STOP
            ctrl.write_text(json.dumps({"command": "STOP"}), encoding="utf-8")
            self.assertEqual(sup.read_control_command(), "STOP")
        finally:
            sup.CONTROL_FILE = orig_ctrl

    def test_health_status_update(self):
        """Verifica serialização de integridade em formato JSON."""
        health_path = self.tmp_path / "health.json"
        orig_health = sup.HEALTH_FILE
        try:
            sup.HEALTH_FILE = health_path
            sup.update_health_status(
                cycle_count=5,
                uptime_seconds=25.0,
                last_status="OK",
                state="ACTIVE",
                ports_status={"8080": True, "18088": False},
                task_counts={"pending": 1, "total": 3, "active": 1},
                gateway_info={"status": "ONLINE", "port": 8080}
            )
            self.assertTrue(health_path.exists())
            data = json.loads(health_path.read_text(encoding="utf-8"))
            self.assertEqual(data["cycle_count"], 5)
            self.assertEqual(data["uptime_seconds"], 25.0)
            self.assertEqual(data["status"], "HEALTHY")
            self.assertEqual(data["state"], "ACTIVE")
            self.assertTrue(data["ports"]["8080"])
            self.assertFalse(data["ports"]["18088"])
            self.assertEqual(data["tasks"]["pending"], 1)
        finally:
            sup.HEALTH_FILE = orig_health

    def test_gateway_fault_tolerance_and_reconnect(self):
        """Verifica recuperação automática quando o processo secundário termina."""
        gw = sup.GatewaySupervisor(host="127.0.0.1", port=18090, enabled=True)
        # Simula processo secundário iniciado que termina imediatamente
        gw.proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(42)"])
        gw.proc.wait()

        # O processo terminou com código 42
        self.assertIsNotNone(gw.proc.poll())
        initial_restarts = gw.restart_count

        # Executa ciclo de manutenção e reconexão (não deve lançar exceção nem travar o host)
        # Sobrescreve start() para simular restart bem sucedido sem abrir porta real
        restarted = False
        def mock_start():
            nonlocal restarted
            restarted = True
            gw.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])

        gw.start = mock_start
        gw.maintain_and_reconnect()

        self.assertTrue(restarted)
        self.assertEqual(gw.restart_count, initial_restarts + 1)
        gw.stop()

    def test_run_continuous_broker_single_cycle_direct(self):
        """Testa a execução direta de 1 ciclo do broker com exit code 0."""
        code = sup.run_continuous_broker(
            max_cycles=1,
            sleep_interval=0.01,
            with_gateway=False
        )
        self.assertEqual(code, 0)

    def test_health_check_function(self):
        """Testa o comando de diagnóstico de saúde."""
        code = sup.run_health_check()
        self.assertEqual(code, 0)

    def test_cli_single_cycle_exit_code_zero(self):
        """Verifica a execução de ciclo único via CLI com código de saída 0."""
        proc = subprocess.run(
            [sys.executable, str(sup.CORE_DIR / "specter_supervisor_247.py"), "--single-cycle", "--no-gateway"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30
        )
        self.assertEqual(proc.returncode, 0, f"Falha na execução CLI: {proc.stderr}")
        self.assertIn("CICLO #1", proc.stdout)
        self.assertIn("SUPERVISOR ENCERRADO", proc.stdout)

    def test_cli_single_cycle_with_gateway(self):
        """Verifica a execução de ciclo único completo com Gateway integrado."""
        proc = subprocess.run(
            [sys.executable, str(sup.CORE_DIR / "specter_supervisor_247.py"), "--single-cycle"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30
        )
        self.assertEqual(proc.returncode, 0, f"Falha na execução CLI com Gateway: {proc.stderr}")
        self.assertIn("Gateway 24/7: ATIVO", proc.stdout)
        self.assertIn("SUPERVISOR ENCERRADO", proc.stdout)


if __name__ == "__main__":
    unittest.main()
