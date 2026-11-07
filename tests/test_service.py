"""Catch side effects on denied/invalid requests and non-atomic persistence."""
import json
import sqlite3
from pathlib import Path

import pytest


def make_service(tmp_path, policy=True):
    from veriagent.models import PolicyBundle
    from veriagent.policy import compile_bundle
    from veriagent.store import Store
    from veriagent.service import SharingService
    store = Store(tmp_path / "demo.sqlite3")
    store.initialize(seed_demo=True)
    bundle = compile_bundle(PolicyBundle.model_validate_json(Path("data/seed_policy.json").read_text())) if policy else None
    return SharingService(store, bundle), store


def count(store, table):
    with sqlite3.connect(store.path) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("doc,recipient,want", [
    ("public-guide", "alice@example.org", "ALLOW"),
    ("confidential-plan", "alice@outside.test", "BLOCK"),
    ("confidential-plan", "alice@example.org", "HUMAN_REVIEW"),
    ("confidential-plan", "manager@example.org", "ALLOW"),
    ("confidential-plan", "manager@outside.test", "BLOCK"),
    ("public-guide", "manager@outside.test", "ALLOW"),
    ("public-guide", "alice@outside.test", "HUMAN_REVIEW"),
    ("unknown-label", "alice@example.org", "HUMAN_REVIEW"),
    ("missing", "alice@example.org", "HUMAN_REVIEW"),
    ("confidential-plan", "manager@example.org.evil.test", "BLOCK"),
    ("confidential-plan", "manager@notexample.org", "BLOCK"),
    ("confidential-plan", "manager@EXAMPLE.ORG", "ALLOW"),
    ("confidential-plan", "Manager@example.org", "HUMAN_REVIEW"),
])
def test_enforced_side_effects(tmp_path, doc, recipient, want):
    service, store = make_service(tmp_path)
    result = service.share_document(doc, recipient)
    assert result.decision == want
    assert result.executed == (want == "ALLOW")
    assert bool(result.outbox_id) == result.executed
    assert result.policy_revision == "seed-v1"
    assert count(store, "outbox") == int(result.executed)
    assert count(store, "decisions") == 1
    with sqlite3.connect(store.path) as db:
        logged = json.loads(db.execute("SELECT result_json FROM decisions").fetchone()[0])
        assert logged["request_id"] == result.request_id
        assert logged["executed"] == result.executed


@pytest.mark.parametrize("recipient", ["", "Alice <alice@example.org>", "a@example.org,b@example.org", "a@@example.org", "a@example.org\n", "a@ example.org", "a@EXAMPLE.ORG.", "a@localhost", "a..b@example.org", "a@ｅxample.org"])
def test_invalid_recipient_pauses(tmp_path, recipient):
    service, store = make_service(tmp_path)
    assert service.share_document("public-guide", recipient).decision == "HUMAN_REVIEW"
    assert count(store, "outbox") == 0


def test_missing_policy_and_invalid_label_fail_closed(tmp_path):
    service, store = make_service(tmp_path, policy=False)
    assert service.share_document("public-guide", "alice@example.org").decision == "HUMAN_REVIEW"
    assert count(store, "outbox") == 0
    service, store = make_service(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE documents SET classification='typo' WHERE id='public-guide'")
    assert service.share_document("public-guide", "alice@example.org").decision == "HUMAN_REVIEW"
    assert count(store, "outbox") == 0


def test_commit_failure_rolls_back_both_tables(tmp_path, monkeypatch):
    from veriagent.service import PersistenceError
    service, store = make_service(tmp_path)
    def fail(_db):
        raise sqlite3.OperationalError("simulated failed commit")
    monkeypatch.setattr(store, "commit", fail)
    with pytest.raises(PersistenceError):
        service.share_document("public-guide", "alice@example.org")
    assert count(store, "outbox") == 0
    assert count(store, "decisions") == 0


def test_audit_write_failure_cannot_leave_an_outbox(tmp_path):
    from veriagent.service import PersistenceError
    service, store = make_service(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER deny_log BEFORE INSERT ON decisions BEGIN SELECT RAISE(ABORT, 'log unavailable'); END")
    with pytest.raises(PersistenceError):
        service.share_document("public-guide", "alice@example.org")
    assert count(store, "outbox") == 0


def test_extra_context_arguments_cannot_reach_service(tmp_path):
    service, store = make_service(tmp_path)
    with pytest.raises(TypeError):
        service.share_document("confidential-plan", "alice@example.org", approved=True)
    assert count(store, "outbox") == 0
