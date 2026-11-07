"""Changed contracts: per-request policy pinning, trusted preview, historical trace."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def seed_home(home):
    from veriagent.authoring import bootstrap_demo
    from veriagent.store import Store
    bootstrap_demo(home)
    store = Store(home / "demo.sqlite3")
    store.initialize(seed_demo=True)
    return store


def review_external(home, revision="updated"):
    from veriagent.authoring import publish_bundle
    from veriagent.models import PolicyBundle
    bundle = PolicyBundle(revision=revision, rules=[dict(id="review-external", source_text="Review external sharing.",
                         effect="HUMAN_REVIEW", condition={"op": "atom", "name": "external"})])
    publish_bundle(bundle, home, {"kind": "test-owner-fixture"})


def test_already_running_mcp_refreshes_next_request(tmp_path):
    seed_home(tmp_path)
    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "veriagent.server", "--home", str(tmp_path)])
        async with Client(params) as client:
            args = {"document_id": "public-guide", "recipient": "manager@outside.test"}
            first = (await client.call_tool("share_document", args)).structured_content
            assert first["decision"] == "ALLOW" and first["policy_revision"] == "seed-v1"
            review_external(tmp_path)
            second = (await client.call_tool("share_document", args)).structured_content
            assert second["decision"] == "HUMAN_REVIEW" and second["policy_revision"] == "updated"
            (tmp_path / "policies/active.json").write_text("{}")
            third = (await client.call_tool("share_document", args)).structured_content
            assert third["decision"] == "HUMAN_REVIEW" and not third["executed"]
    asyncio.run(check())


def test_request_pins_revision_and_activity_keeps_original_facts(tmp_path, monkeypatch):
    from veriagent.service import SharingService
    from veriagent.policy import load_snapshot
    store = seed_home(tmp_path)
    service = SharingService(store, home=tmp_path)
    import veriagent.service as module
    actual_decide = module.decide
    def change_during_request(bundle, context):
        review_external(tmp_path)
        return actual_decide(bundle, context)
    monkeypatch.setattr(module, "decide", change_during_request)
    result = service.share_document("public-guide", "manager@outside.test")
    assert result.policy_revision == "seed-v1" and result.executed
    assert load_snapshot(tmp_path).revision == "updated"
    row = store.activity()[0]
    assert row["facts"] == {"confidential": False, "external": True, "approved": True}
    assert row["policy_revision"] == "seed-v1"
    assert row["trace"]["rules"][0]["query"]["assumptions"] == [-1, 2, 3]
    assert row["trace"]["rules"][0]["clauses"] == [[1], [2]]


def test_preview_has_no_live_side_effects(tmp_path):
    from veriagent.service import SharingService
    store = seed_home(tmp_path)
    service = SharingService(store, home=tmp_path)
    before = store.inspect()
    preview = service.preview("confidential-plan", "alice@example.org")
    assert preview["decision"] == "HUMAN_REVIEW"
    assert preview["facts"] == {"confidential": True, "external": False, "approved": False}
    assert store.inspect() == before
    invalid = service.preview("unknown-label", "alice@example.org")
    assert invalid["facts"] is None


def test_state_resolution_explicit_selection_and_installed_default(tmp_path, monkeypatch):
    from veriagent.state import resolve_home, select_home
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("VERIAGENT_HOME", raising=False)
    assert resolve_home() == tmp_path / "appdata/VeriAgent/state"
    existing = tmp_path / "owner"
    seed_home(existing)
    select_home(existing)
    assert resolve_home() == existing.resolve()
    monkeypatch.setenv("VERIAGENT_HOME", str(tmp_path / "explicit-env"))
    assert resolve_home() == tmp_path / "explicit-env"
    assert resolve_home(tmp_path / "argument") == tmp_path / "argument"
