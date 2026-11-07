"""Removing trusted metadata must not reinstate seeded permissions on restart."""
import asyncio
import sqlite3
import sys

from mcp import Client, StdioServerParameters


def test_restart_preserves_removed_catalog_and_approvals(tmp_path):
    from veriagent.authoring import bootstrap_demo
    from veriagent.store import Store
    bootstrap_demo(tmp_path)
    store = Store(tmp_path / "demo.sqlite3")
    store.initialize(seed_demo=True)
    with store.transaction() as db:
        db.execute("DELETE FROM approvals")
        db.execute("DELETE FROM documents")

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "veriagent.server"],
                                      env={"VERIAGENT_HOME": str(tmp_path)})
        async with Client(params) as client:
            catalog = await client.call_tool("list_documents", {})
            assert catalog.structured_content["result"] == []
            share = await client.call_tool("share_document", {"document_id": "confidential-plan", "recipient": "manager@example.org"})
            assert share.structured_content["decision"] == "HUMAN_REVIEW"
            assert not share.structured_content["executed"]
        assert store.inspect()["outbox"] == []
    asyncio.run(check())


def test_missing_database_with_retained_policy_cannot_restore_permissions(tmp_path):
    from veriagent.authoring import bootstrap_demo
    from veriagent.store import Store
    from veriagent.policy import load_snapshot
    from veriagent.service import SharingService
    bootstrap_demo(tmp_path)
    store = Store(tmp_path / "missing.sqlite3")
    store.initialize()
    result = SharingService(store, load_snapshot(tmp_path)).share_document("confidential-plan", "manager@example.org")
    assert result.decision == "HUMAN_REVIEW"
    assert store.list_documents() == []
    assert store.inspect()["outbox"] == []
