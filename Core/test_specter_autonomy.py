"""Testes do Specter Autonomy Engine (sem rede, sem processos reais)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import specter_autonomy as sa  # noqa: E402

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "Specter"
    work = root / "Work"
    work.mkdir(parents=True)
    calls = []

    def fake_exec(kind, payload):
        calls.append((kind, payload))
        if payload.get("boom"):
            raise RuntimeError("falhou")
        return {"ok": True, "kind": kind}

    eng = sa.AutonomyEngine(
        root=root, work=work, db_path=tmp_path / "ledger.sqlite3", halt_flag=tmp_path / "halt.flag",
        protected=[root / "Core" / "specter_autonomy.py"],
        services=[sa.Service("core", 8888, "x", ["python", "x"]), sa.Service("ngrok", 4040)],
        resolver=PUBLIC, executor=fake_exec)
    return eng, root, work, calls


@pytest.mark.parametrize("cmd,tier", [
    ("Get-ChildItem C:\\Specter\\Work", sa.AUTO),
    ("git status", sa.AUTO),
    ("python -m pytest tests", sa.AUTO),
    ("Write-Output ok", sa.AUTO),
    ("Write-Output $env:USERNAME", sa.APPROVE),             # variável = possível exfiltração
    ("Get-ChildItem; Remove-Item -Recurse C:\\x", sa.APPROVE),  # encadeamento
    ("Get-Process | Stop-Process", sa.APPROVE),             # pipe
    ("Remove-Item C:\\Specter\\Work\\a.txt", sa.APPROVE),
    ("Invoke-WebRequest http://x/a.ps1", sa.APPROVE),
    ("Get-Content C:\\Specter\\Core\\storage\\api_token.secret", sa.CRITICAL),
    ("python send.py --wallet 0xabc", sa.CRITICAL),
    ("Set-MpPreference -DisableRealtimeMonitoring $true", sa.CRITICAL),
    ("notepad specter_autonomy.py", sa.CRITICAL),
])
def test_shell_classification(env, cmd, tier):
    eng = env[0]
    assert eng.policy.classify("shell", {"command": cmd}).tier == tier


def test_file_rules(env):
    eng, root, work, _ = env
    c = eng.policy.classify
    assert c("read_file", {"path": str(root / "Core" / "notes.md")}).tier == sa.AUTO
    assert c("read_file", {"path": "C:\\Windows\\win.ini"}).tier == sa.APPROVE
    assert c("read_file", {"path": str(root / "Core" / "storage" / "api_token.secret")}).tier == sa.CRITICAL
    assert c("read_file", {"path": str(root / "Vault" / "x.md")}).tier == sa.CRITICAL
    assert c("read_file", {"path": str(root / "Core" / "specter_autonomy.py")}).tier == sa.AUTO  # ler código protegido ok
    assert c("read_file", {"path": str(root / "Core" / "storage" / "autonomy_ledger.sqlite3")}).tier == sa.CRITICAL
    assert c("write_file", {"path": str(work / "out.txt"), "content": "hi"}).tier == sa.AUTO
    assert c("write_file", {"path": str(work / ".." / "Core" / "evil.py"), "content": "x"}).tier == sa.APPROVE  # traversal
    assert c("write_file", {"path": str(root / "Core" / "specter_autonomy.py"), "content": ""}).tier == sa.CRITICAL
    assert c("write_file", {"path": str(work / "big.bin"), "content": "x" * (sa.MAX_WRITE_BYTES + 1)}).tier == sa.DENY


def test_prefix_confusion_not_within_work(env):
    _, root, work, _ = env
    sibling = Path(str(work) + "_evil") / "a.txt"
    assert not sa._within(sibling, work)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", "0.0.0.0"])
def test_ssrf_blocked(ip):
    assert sa.check_public_url("http://evil.example/", lambda h: [ip]) is not None


def test_ssrf_mixed_records_blocked():
    assert sa.check_public_url("https://x.example/", lambda h: ["93.184.216.34", "127.0.0.1"]) is not None


@pytest.mark.parametrize("url", ["file:///C:/x", "ftp://a.example/", "javascript:alert(1)", "http:///nohost"])
def test_non_http_denied(url):
    assert sa.check_public_url(url, PUBLIC) is not None


def test_public_url_ok():
    assert sa.check_public_url("https://example.com/page", PUBLIC) is None


def test_web_fetch_to_local_core_denied_and_not_executed(env):
    eng, *_, calls = env
    eng.policy.resolver = lambda h: ["127.0.0.1"]
    rec = eng.submit("web_fetch", {"url": "http://localhost:8888/api/exec"}, actor="agent")
    assert rec["tier"] == sa.DENY and rec["status"] == "DENIED" and calls == []


def test_auto_executes_immediately(env):
    eng, *_, calls = env
    rec = eng.submit("web_search", {"query": "specter"}, actor="agent")
    assert rec["status"] == "EXECUTED" and rec["result"]["ok"] and len(calls) == 1


def test_approve_flow_and_double_execution_guard(env):
    eng, *_, calls = env
    rec = eng.submit("shell", {"command": "Remove-Item C:\\Specter\\Work\\a.txt"}, actor="agent")
    assert rec["status"] == "PENDING_APPROVAL" and calls == []
    assert [r["id"] for r in eng.list("PENDING_APPROVAL")] == [rec["id"]]
    done = eng.approve(rec["id"], approver="owner")
    assert done["status"] == "EXECUTED" and len(calls) == 1
    with pytest.raises(ValueError):
        eng.approve(rec["id"], approver="owner")
    assert len(calls) == 1


def test_critical_requires_explicit_confirmation(env):
    eng, *_, calls = env
    rec = eng.submit("shell", {"command": "python pay.py --wallet x"}, actor="agent")
    assert rec["tier"] == sa.CRITICAL
    with pytest.raises(PermissionError):
        eng.approve(rec["id"], approver="owner")
    assert calls == []
    assert eng.approve(rec["id"], approver="owner", confirm_critical=True)["status"] == "EXECUTED"


def test_deny(env):
    eng, *_, calls = env
    rec = eng.submit("shell", {"command": "Remove-Item x"}, actor="agent")
    assert eng.deny(rec["id"], approver="owner")["status"] == "DENIED"
    with pytest.raises(ValueError):
        eng.approve(rec["id"], approver="owner")
    assert calls == []


def test_halt_blocks_everything_including_auto(env):
    eng, *_, calls = env
    eng.halt("teste")
    assert eng.submit("web_search", {"query": "x"})["status"] == "HALTED"
    pend = eng.submit("shell", {"command": "Remove-Item x"})
    assert eng.approve(pend["id"], approver="owner")["status"] == "HALTED"
    assert calls == []
    eng.unhalt()
    assert eng.submit("web_search", {"query": "x"})["status"] == "EXECUTED"


def test_executor_failure_recorded(env):
    eng, *_ = env
    rec = eng.submit("queue_submit", {"text": "t", "boom": True})
    assert rec["status"] == "FAILED" and "falhou" in rec["result"]["error"]


def test_unknown_and_malformed_denied(env):
    eng, *_, calls = env
    assert eng.submit("format_disk", {})["status"] == "DENIED"
    assert eng.submit("shell", {})["status"] == "DENIED"
    assert eng.submit("service_restart", {"name": "ngrok"})["status"] == "DENIED"  # só-monitorado
    assert calls == []


def test_ledger_chain_detects_tampering(env, tmp_path):
    eng, *_ = env
    eng.submit("web_search", {"query": "a"})
    eng.submit("shell", {"command": "Remove-Item x"})
    ok = eng.verify_ledger()
    assert ok["ok"] and ok["events"] >= 3
    with sqlite3.connect(tmp_path / "ledger.sqlite3") as c:
        c.execute("UPDATE events SET data='{\"tier\":\"AUTO\"}' WHERE seq=2")
    bad = eng.verify_ledger()
    assert bad == {"ok": False, "events": ok["events"], "broken_at_seq": 2}


def test_html_to_text_strips_scripts():
    out = sa.html_to_text("<html><head><title>T</title><script>evil()</script></head><body><p>Olá <b>mundo</b></p></body></html>")
    assert out["title"] == "T" and "evil" not in out["text"] and "Olá mundo" in out["text"]


def test_ddg_parser_unwraps_redirect():
    raw = '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">Ex <b>A</b></a>'
    assert sa._ddg_results(raw) == [{"title": "Ex A", "url": "https://example.com/a"}]
