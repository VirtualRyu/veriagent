"""Observed status, explicit home and old audit records stay truthful."""
import asyncio
import json
import sys

from mcp import Client, StdioServerParameters
from test_desktop_foundation import seed_home


def test_real_mcp_process_reports_observed_home_and_request(tmp_path, monkeypatch):
    from veriagent.connection import status
    monkeypatch.setattr("veriagent.connection.find_codex", lambda: None)
    seed_home(tmp_path)
    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "veriagent.server", "--home", str(tmp_path)],
                                      env={"VERIAGENT_HOME": str(tmp_path)})
        async with Client(params) as client:
            await client.call_tool("list_documents", {})
            observed = status(tmp_path)
            assert observed["processes"][0]["home"] == str(tmp_path.resolve())
            assert observed["last_request"]["tool"] == "list_documents"
            assert observed["configuration"].startswith("Setup required")
    asyncio.run(check())


def test_legacy_audit_has_no_invented_historical_trace(tmp_path):
    from veriagent.models import ActionResult
    store = seed_home(tmp_path)
    result = ActionResult(request_id="older", decision="HUMAN_REVIEW", executed=False,
                          policy_revision="old-policy", reason="Preserved original reason")
    with store.transaction() as db:
        store.record(db, result, "unknown-label", "alice@example.org")
    old = store.activity()[0]
    assert old["facts"] is None and old["trace"] is None
    assert old["policy_revision"] == "old-policy"
    assert old["reason"] == "Preserved original reason"


def test_existing_registration_with_null_environment_is_reported_not_crashed(tmp_path, monkeypatch):
    import subprocess
    from veriagent.connection import status
    monkeypatch.setattr("veriagent.connection.find_codex", lambda: "codex.exe")
    monkeypatch.setattr("veriagent.connection.run", lambda _args: subprocess.CompletedProcess([], 0,
        stdout=json.dumps({"enabled": True, "transport": {"type": "stdio", "env": None,
                          "args": ["-NoProfile", "-File", str(tmp_path / "start_server.ps1")]}}), stderr=""))
    assert status(tmp_path)["configuration"].startswith("Attention required")
