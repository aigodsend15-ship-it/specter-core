# -*- coding: utf-8 -*-
"""
SPECTER SOVEREIGN TERMINAL // POWERSHELL INTERACTIVE HUB v5.1
=============================================================
Console interativo em tempo real via terminal PowerShell.
- Gera automaticamente arquivo .txt de histórico por sessão em History/Sessions/
- Exibe de forma destacada a URL do túnel persistente para compartilhamento
- Captura em tempo real mensagens de agentes conectados via túnel (Grok, Spark, Claude, ChatGPT)
- Permite envio imediato de mensagens/tarefas aos agentes com registro em disco
- Extremamente leve (<0.1% CPU, baixo footprint de memória, zero spam)
"""

import os
import sys
import time
import socket
import sqlite3
import datetime
import threading
from pathlib import Path
from typing import Optional, List, Tuple

# Força codificação UTF-8 para console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Cores ANSI
C_RESET = "\033[0m"
C_CYAN = "\033[1;36m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_MAGENTA = "\033[1;35m"
C_WHITE = "\033[1;37m"
C_GRAY = "\033[90m"
C_RED = "\033[1;31m"
C_BG_BLUE = "\033[44m"

# Diretórios configuráveis por ambiente
CORE_DIR = Path(os.getenv("SPECTER_CORE_DIR", Path(__file__).resolve().parent))
STORAGE_DIR = Path(os.getenv("SPECTER_STORAGE_DIR", CORE_DIR / "storage"))
DB_PATH = Path(os.getenv("SPECTER_DB_PATH", STORAGE_DIR / "specter_fabric.sqlite3"))
TUNNEL_URL_FILE = Path(os.getenv("SPECTER_TUNNEL_FILE", STORAGE_DIR / "public_tunnel_url.txt"))
HISTORY_DIR = Path(os.getenv("SPECTER_HISTORY_DIR", CORE_DIR.parent / "History"))
SESSIONS_DIR = HISTORY_DIR / "Sessions"
EXCHANGE_DIR = Path(os.getenv("SPECTER_EXCHANGE_DIR", CORE_DIR.parent / "Exchange" / "incoming"))

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


class SpecterSessionLogger:
    """Gerencia a persistência atômica do histórico da sessão em arquivo .txt."""
    def __init__(self, tunnel_url: str):
        self.tunnel_url = tunnel_url
        self.start_dt = datetime.datetime.now()
        timestamp_str = self.start_dt.strftime("%Y-%m-%d_%H%M%S")
        self.session_filename = f"session_{timestamp_str}.txt"
        self.session_path = SESSIONS_DIR / self.session_filename
        self.latest_pointer = HISTORY_DIR / "session_latest.txt"
        self._lock = threading.Lock()
        self._init_session_file()

    def _init_session_file(self):
        operator = os.getenv("SPECTER_OPERATOR", "Guilherme Peralta Novaes")
        header = (
            "=" * 80 + "\n"
            "SPECTER SOVEREIGN ECOSYSTEM — REGISTRO HISTÓRICO DE SESSÃO (POWERSHELL HUB)\n"
            "=" * 80 + "\n"
            f"Data e Hora de Inicio : {self.start_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Host / Ambiente       : {sys.platform} (Specter Sovereign Kernel)\n"
            f"Operador Supremo      : {operator} (L0 Sovereign)\n"
            f"Tunel Persistente     : {self.tunnel_url}\n"
            f"Arquivo da Sessao     : {self.session_path}\n"
            f"Endpoints da Malha    : POST /api/message | POST /v1/federation/exec | POST /v1/federation/submit_code\n"
            "=" * 80 + "\n"
            "[LOG DE COMUNICAÇÃO & AÇÕES EM TEMPO REAL]\n\n"
        )
        with open(self.session_path, "w", encoding="utf-8") as f:
            f.write(header)

        # Atualiza ponteiro para a sessão mais recente
        try:
            with open(self.latest_pointer, "w", encoding="utf-8") as f:
                f.write(f"Arquivo da sessao atual: {self.session_path}\nIniciada em: {self.start_dt}\nTunel: {self.tunnel_url}\n")
        except Exception:
            pass

    def log_event(self, category: str, sender: str, receiver: str, content: str):
        """Registra um evento formatado com timestamp no .txt."""
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{now_str}] [{category}] {sender} -> {receiver}:\n  {content}\n"
        with self._lock:
            try:
                with open(self.session_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception as e:
                print(f"[!] Erro ao gravar log: {e}")

    def log_action(self, category: str, details: str):
        """Registra ação de sistema ou envio de código."""
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{now_str}] [{category}] {details}\n"
        with self._lock:
            try:
                with open(self.session_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass


def get_tunnel_url() -> str:
    """Lê a URL pública do túnel configurado ou ambiente."""
    env_url = os.getenv("SPECTER_TUNNEL_URL")
    if env_url:
        return env_url.strip()
    if TUNNEL_URL_FILE.exists():
        try:
            url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip()
            if url.startswith("http"):
                return url
        except Exception:
            pass
    return "http://127.0.0.1:8888 (Local Gateway)"


def get_connected_nodes() -> List[Tuple[str, str, str, str]]:
    """Retorna os nós conectados registrados no SQLite."""
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        cur = conn.cursor()
        cur.execute("SELECT node_id, alias, role, status FROM active_nodes ORDER BY last_seen DESC LIMIT 15")
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def post_local_message(sender: str, receiver: str, text: str) -> bool:
    """Insere diretamente no ledger SQLite com hash sha256 e aciona o barramento."""
    import hashlib
    try:
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS swarm_dialogue_ledger (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                sender TEXT,
                receiver TEXT,
                dsl_message TEXT,
                hash_digest TEXT
            )
        """)
        now_ts = time.time()
        digest = hashlib.sha256(f"{now_ts}:{sender}:{receiver}:{text}".encode("utf-8")).hexdigest()
        cur.execute("""
            INSERT INTO swarm_dialogue_ledger (timestamp, sender, receiver, dsl_message, hash_digest)
            VALUES (?, ?, ?, ?, ?)
        """, (now_ts, sender, receiver, text, digest))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[!] Erro ao salvar mensagem no SQLite: {e}")
        return False


class SpecterTerminalHub:
    """Controlador central do Terminal PowerShell."""
    def __init__(self):
        self.tunnel_url = get_tunnel_url()
        self.logger = SpecterSessionLogger(self.tunnel_url)
        self.running = True
        self.last_turn_id = self._get_latest_turn_id()
        self.seen_incoming_files = set(f.name for f in EXCHANGE_DIR.glob("*") if f.is_file())

    def _get_latest_turn_id(self) -> int:
        if not DB_PATH.exists():
            return 0
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=3)
            res = conn.execute("SELECT MAX(turn_id) FROM swarm_dialogue_ledger").fetchone()
            conn.close()
            return res[0] if res and res[0] else 0
        except Exception:
            return 0

    def print_banner(self):
        """Imprime o cabeçalho moderno no terminal PowerShell."""
        os.system("cls" if os.name == "nt" else "clear")
        operator = os.getenv("SPECTER_OPERATOR", "Guilherme Peralta Novaes")
        print(f"{C_CYAN}{'=' * 82}{C_RESET}")
        print(f"{C_WHITE}      ⚡ SPECTER SOVEREIGN CONSOLE // POWERSHELL INTERACTIVE HUB v5.1 ⚡{C_RESET}")
        print(f"{C_CYAN}{'=' * 82}{C_RESET}")
        print(f"  {C_WHITE}OPERADOR SOBERANO   :{C_RESET} {C_GREEN}{operator} (L0 Sovereign){C_RESET}")
        print(f"  {C_WHITE}HISTÓRICO DA SESSÃO :{C_RESET} {C_YELLOW}{self.logger.session_path}{C_RESET}")
        print(f"{C_CYAN}{'-' * 82}{C_RESET}")
        print(f"  {C_GREEN}>>> TÚNEL PERSISTENTE (COMPARTILHE COM SEUS CHATBOTS/AGENTES): <<<{C_RESET}")
        print(f"  {C_CYAN}    {self.tunnel_url}{C_RESET}")
        print(f"{C_CYAN}{'-' * 82}{C_RESET}")
        print(f"  {C_WHITE}COMANDOS DISPONÍVEIS:{C_RESET}")
        print(f"    {C_YELLOW}/nodes{C_RESET}     - Listar agentes conectados e status da malha")
        print(f"    {C_YELLOW}/tunnel{C_RESET}    - Reexibir a URL do túnel público persistente")
        print(f"    {C_YELLOW}/history{C_RESET}   - Ver últimas mensagens registradas nesta sessão")
        print(f"    {C_YELLOW}/log{C_RESET}       - Abrir o arquivo de histórico .txt no Bloco de Notas")
        print(f"    {C_YELLOW}/clear{C_RESET}     - Limpar tela e reexibir o painel")
        print(f"    {C_YELLOW}/exit{C_RESET}      - Encerrar o console com segurança")
        print(f"  {C_GRAY}Dica: Digite sua mensagem normalmente para transmitir aos agentes.{C_RESET}")
        print(f"  {C_GRAY}      Para direcionar: @Grok: sua mensagem aqui{C_RESET}")
        print(f"{C_CYAN}{'=' * 82}{C_RESET}\n")

    def start_background_watcher(self):
        """Thread que monitora novas mensagens e arquivos recebidos sem bloquear o teclado."""
        operator = os.getenv("SPECTER_OPERATOR", "Guilherme Peralta Novaes")
        first_name = operator.split()[0]

        def _watcher():
            while self.running:
                try:
                    if DB_PATH.exists():
                        conn = sqlite3.connect(str(DB_PATH), timeout=3)
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT turn_id, timestamp, sender, receiver, dsl_message 
                            FROM swarm_dialogue_ledger 
                            WHERE turn_id > ? 
                            ORDER BY turn_id ASC
                        """, (self.last_turn_id,))
                        rows = cur.fetchall()
                        conn.close()

                        for tid, ts, sender, receiver, msg in rows:
                            self.last_turn_id = tid
                            if "OWNER" in sender:
                                continue

                            time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                            color = C_MAGENTA if "Grok" in sender else (C_CYAN if "Spark" in sender or "Hermes" in sender else C_YELLOW)
                            
                            print(f"\n{C_GRAY}[{time_str}]{C_RESET} {color}[{sender} -> {receiver}]{C_RESET}:")
                            print(f"  {C_WHITE}{msg}{C_RESET}\n")
                            sys.stdout.write(f"{C_GREEN}{first_name} > {C_RESET}")
                            sys.stdout.flush()

                            self.logger.log_event("MENSAGEM", sender, receiver, msg)

                    if EXCHANGE_DIR.exists():
                        current_files = set(f.name for f in EXCHANGE_DIR.glob("*") if f.is_file())
                        new_files = current_files - self.seen_incoming_files
                        for nf in new_files:
                            self.seen_incoming_files.add(nf)
                            fpath = EXCHANGE_DIR / nf
                            size_kb = round(fpath.stat().st_size / 1024, 2)
                            notice = f"Recebido arquivo '{nf}' ({size_kb} KB) em {EXCHANGE_DIR}"
                            print(f"\n{C_YELLOW}📥 [CÓDIGO RECEBIDO VIA TÚNEL]{C_RESET} {notice}")
                            sys.stdout.write(f"{C_GREEN}{first_name} > {C_RESET}")
                            sys.stdout.flush()
                            self.logger.log_action("INCOMING_CODE", notice)

                except Exception:
                    pass

                time.sleep(0.8)

        t = threading.Thread(target=_watcher, daemon=True)
        t.start()

    def show_nodes(self):
        nodes = get_connected_nodes()
        print(f"\n{C_CYAN}--- AGENTES & NÓS CONECTADOS NA MALHA SPECTER ---{C_RESET}")
        if not nodes:
            print(f"  {C_GRAY}Nenhum nó registrado no momento.{C_RESET}")
        else:
            for nid, alias, role, status in nodes:
                st_color = C_GREEN if status == "ONLINE" else C_GRAY
                print(f"  • {C_WHITE}{alias or nid}{C_RESET} ({C_GRAY}{role or 'Agente'}{C_RESET}) - {st_color}[{status}]{C_RESET}")
        print(f"{C_CYAN}-------------------------------------------------{C_RESET}\n")

    def show_history(self):
        if not self.logger.session_path.exists():
            print(f"{C_RED}[!] Arquivo de sessão ainda não gerado.{C_RESET}")
            return
        lines = self.logger.session_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        print(f"\n{C_CYAN}--- ÚLTIMAS LINHAS DA SESSÃO ATUAL ({self.logger.session_filename}) ---{C_RESET}")
        for l in lines[-25:]:
            print(f"  {l}")
        print(f"{C_CYAN}---------------------------------------------------------------------{C_RESET}\n")

    def run_interactive_loop(self):
        self.print_banner()
        self.start_background_watcher()
        operator = os.getenv("SPECTER_OPERATOR", "Guilherme Peralta Novaes")
        first_name = operator.split()[0]

        while self.running:
            try:
                user_input = input(f"{C_GREEN}{first_name} > {C_RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue

            cmd_lower = user_input.lower()
            if cmd_lower in ("/exit", "/quit", "exit", "sair"):
                print(f"\n{C_YELLOW}[*] Encerrando Specter Console. Histórico preservado em:{C_RESET}")
                print(f"    {self.logger.session_path}\n")
                self.running = False
                break
            elif cmd_lower in ("/clear", "cls", "clear"):
                self.print_banner()
            elif cmd_lower == "/nodes":
                self.show_nodes()
            elif cmd_lower == "/tunnel":
                print(f"\n{C_GREEN}>>> URL DO TÚNEL PERSISTENTE: <<<{C_RESET}")
                print(f"  {C_CYAN}{self.tunnel_url}{C_RESET}\n")
            elif cmd_lower == "/history":
                self.show_history()
            elif cmd_lower == "/log":
                if sys.platform == "win32":
                    os.system(f'notepad "{self.logger.session_path}"')
                else:
                    print(f"Arquivo de log: {self.logger.session_path}")
            elif cmd_lower == "/help":
                print(f"\n{C_WHITE}Comandos: /nodes, /tunnel, /history, /log, /clear, /exit{C_RESET}")
                print("Para falar com agentes: digite a mensagem diretamente.")
                print("Para direcionar: @Grok: sua mensagem aqui\n")
            else:
                receiver = "TODOS_AGENTES"
                msg_body = user_input
                if user_input.startswith("@"):
                    parts = user_input.split(":", 1)
                    if len(parts) == 2:
                        receiver = parts[0][1:].strip()
                        msg_body = parts[1].strip()

                sender_name = f"OWNER ({first_name})"
                success = post_local_message(sender_name, receiver, msg_body)
                if success:
                    print(f"  {C_GREEN}[✓ Mensagem enviada para {receiver}]{C_RESET}")
                    self.logger.log_event("ENVIO_OWNER", sender_name, receiver, msg_body)
                else:
                    print(f"  {C_RED}[!] Falha ao gravar mensagem no barramento.{C_RESET}")


def main():
    hub = SpecterTerminalHub()
    hub.run_interactive_loop()


if __name__ == "__main__":
    main()
