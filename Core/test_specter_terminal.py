# -*- coding: utf-8 -*-
"""
Suíte de Testes Unitários do Specter Terminal Hub & PowerShell Session Logger
"""

import sys
import time
import pytest
from pathlib import Path

# Inclusão dinâmica do diretório Core
CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from specter_terminal import (
    SpecterSessionLogger,
    SpecterTerminalHub,
    post_local_message,
    get_connected_nodes,
    get_tunnel_url
)

def test_session_logger_creates_file_with_valid_header(tmp_path):
    tunnel = "https://test-tunnel.example.com"
    logger = SpecterSessionLogger(tunnel_url=tunnel)
    assert logger.session_path.exists()
    content = logger.session_path.read_text(encoding="utf-8")
    assert "SPECTER SOVEREIGN ECOSYSTEM" in content
    assert tunnel in content

def test_session_logger_event_and_action_recording():
    logger = SpecterSessionLogger(tunnel_url="https://test.example.com")
    logger.log_event("CHAT", "Grok", "OWNER", "Teste de mensagem direta do Grok")
    logger.log_action("CODE_SUBMIT", "Arquivo modulo_teste.py gravado")
    
    content = logger.session_path.read_text(encoding="utf-8")
    assert "[CHAT] Grok -> OWNER:" in content
    assert "Teste de mensagem direta do Grok" in content
    assert "[CODE_SUBMIT] Arquivo modulo_teste.py gravado" in content

def test_post_local_message_stores_in_sqlite():
    sender = "UnitTester"
    receiver = "TODOS_AGENTES"
    text = "Validacao automatizada do barramento"
    res = post_local_message(sender, receiver, text)
    assert res is True

def test_get_tunnel_url_returns_valid_string():
    url = get_tunnel_url()
    assert isinstance(url, str)
    assert len(url) > 5

def test_terminal_hub_properties():
    hub = SpecterTerminalHub()
    assert hub.tunnel_url is not None
    assert hub.logger.session_path.exists()
    assert hub.running is True
