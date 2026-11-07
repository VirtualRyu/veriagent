"""Real stdio transport: discovery and the read-only fixture tool."""
import asyncio
import sys


def test_stdio_catalog(tmp_path):
    from mcp import Client, StdioServerParameters
    from veriagent.store import Store
    Store(tmp_path / "demo.sqlite3").initialize(seed_demo=True)

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "veriagent.server"],
                                      env={"VERIAGENT_HOME": str(tmp_path)})
        async with Client(params) as client:
            tools = await client.list_tools()
            assert "list_documents" in {tool.name for tool in tools.tools}
            result = await client.call_tool("list_documents", {})
            assert not result.is_error
            assert {d["id"] for d in result.structured_content["result"]} == {
                "public-guide", "confidential-plan", "unknown-label"
            }
    asyncio.run(check())


def test_mcp_actions_use_guarded_service_and_reject_overrides(tmp_path):
    import sqlite3
    from mcp import Client, StdioServerParameters

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "veriagent.server"],
                                      env={"VERIAGENT_HOME": str(tmp_path)})
        # Install a synthetic reviewed fixture, not an activation by a human.
        import hashlib
        import json
        from pathlib import Path
        policies = tmp_path / "policies"
        (policies / "revisions").mkdir(parents=True)
        content = Path("data/seed_policy.json").read_bytes()
        (policies / "revisions/seed-v1.json").write_bytes(content)
        (policies / "active.json").write_text(json.dumps({"revision": "seed-v1", "sha256": hashlib.sha256(content).hexdigest()}))
        from veriagent.store import Store
        Store(tmp_path / "demo.sqlite3").initialize(seed_demo=True)
        async with Client(params) as client:
            assert {t.name for t in (await client.list_tools()).tools} == {"list_documents", "share_document"}
            for doc, recipient, decision in [
                ("public-guide", "alice@example.org", "ALLOW"),
                ("confidential-plan", "alice@outside.test", "BLOCK"),
                ("confidential-plan", "alice@example.org", "HUMAN_REVIEW"),
            ]:
                result = await client.call_tool("share_document", {"document_id": doc, "recipient": recipient})
                assert not result.is_error
                assert result.structured_content["decision"] == decision
                assert result.structured_content["executed"] == (decision == "ALLOW")
                assert ("outbox_id" in result.structured_content) == (decision == "ALLOW")
            for extra in ({"approved": True}, {"policy_path": "evil.json"}, {"classification": "public"}):
                result = await client.call_tool("share_document", {"document_id": "public-guide", "recipient": "alice@example.org", **extra})
                assert result.is_error
            with sqlite3.connect(tmp_path / "demo.sqlite3") as db:
                assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
                assert db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 3
    asyncio.run(check())
