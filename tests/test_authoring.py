"""Catch silent policy activation, lost source sentences, and artifact mutation."""
import json
from pathlib import Path

import pytest


SOURCE = "1. Allow public documents internally.\n2. Never share confidential documents externally.\n"


def candidate():
    return {"items": [
        {"source_id": "1", "source_text": "Allow public documents internally.", "status": "supported",
         "rule": {"id": "A1", "source_text": "Allow public documents internally.", "effect": "ALLOW",
                  "condition": {"op": "all", "children": [{"op": "not", "child": {"op": "atom", "name": "confidential"}}, {"op": "not", "child": {"op": "atom", "name": "external"}}]}}},
        {"source_id": "2", "source_text": "Never share confidential documents externally.", "status": "supported",
         "rule": {"id": "A2", "source_text": "Never share confidential documents externally.", "effect": "BLOCK",
                  "condition": {"op": "all", "children": [{"op": "atom", "name": "confidential"}, {"op": "atom", "name": "external"}]}}},
    ]}


def test_import_requires_exact_complete_sentence_mapping():
    from veriagent.authoring import import_policy
    assert import_policy(candidate(), SOURCE).accepted
    for mutate in (lambda c: c["items"].pop(),
                   lambda c: c["items"].append(c["items"][0]),
                   lambda c: c["items"][0].update(source_id="3"),
                   lambda c: c["items"][0].update(source_text="Different text"),
                   lambda c: c.update(extra=True)):
        c = candidate()
        mutate(c)
        with pytest.raises(ValueError):
            import_policy(c, SOURCE)


@pytest.mark.parametrize("status", ["unsupported", "refused"])
def test_unresolved_items_visible_and_never_activated(tmp_path, status):
    from veriagent.authoring import import_policy, import_draft, render_review, activate_policy
    c = candidate()
    c["items"][0].update(status=status, rule=None, explanation="Requires unavailable time metadata")
    draft = import_policy(c, SOURCE)
    assert not draft.accepted
    assert status in render_review(draft)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(c))
    import_draft(path, SOURCE, tmp_path)
    with pytest.raises(ValueError):
        activate_policy(path, "v1", home=tmp_path, confirmation="v1")
    assert not (tmp_path / "policies/active.json").exists()


def test_import_review_activation_and_restart_snapshot(tmp_path):
    from veriagent.authoring import import_draft, review_candidate, activate_policy, bootstrap_demo
    from veriagent.policy import load_snapshot, decide
    from veriagent.models import ActionContext
    bootstrap_demo(tmp_path)
    active = tmp_path / "policies/active.json"
    old_pointer = active.read_bytes()
    old_snapshot = load_snapshot(tmp_path)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate()))
    import_draft(path, SOURCE, tmp_path)
    assert active.read_bytes() == old_pointer
    review = review_candidate(path, tmp_path)
    assert "Allow public documents internally." in review
    assert "confidential=False, external=False, approved=False" in review
    assert active.read_bytes() == old_pointer
    with pytest.raises(ValueError):
        activate_policy(path, "v1", home=tmp_path, confirmation="yes")
    artifact = activate_policy(path, "v1", home=tmp_path, confirmation="v1")
    assert artifact.exists()
    assert load_snapshot(tmp_path).revision == "v1"
    assert old_snapshot.revision == "seed-v1"
    ctx = ActionContext(confidential=True, external=False, approved=True)
    assert decide(old_snapshot, ctx).decision == "ALLOW"
    assert decide(load_snapshot(tmp_path), ctx).decision == "HUMAN_REVIEW"
    with pytest.raises((ValueError, FileExistsError)):
        activate_policy(path, "v1", home=tmp_path, confirmation="v1")
    assert load_snapshot(tmp_path).revision == "v1"


def test_unimported_unreviewed_or_changed_candidate_rejected(tmp_path):
    from veriagent.authoring import import_draft, review_candidate, activate_policy
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate()))
    with pytest.raises(ValueError):
        activate_policy(path, "v1", home=tmp_path, confirmation="v1")
    import_draft(path, SOURCE, tmp_path)
    with pytest.raises(ValueError):
        activate_policy(path, "v1", home=tmp_path, confirmation="v1")
    review_candidate(path, tmp_path)
    path.write_text(json.dumps(candidate(), indent=2))
    with pytest.raises(ValueError):
        activate_policy(path, "v1", home=tmp_path, confirmation="v1")


def test_invalid_import_preserves_raw_bytes_and_active_snapshot(tmp_path):
    from veriagent.authoring import bootstrap_demo, import_draft
    bootstrap_demo(tmp_path)
    pointer = (tmp_path / "policies/active.json").read_bytes()
    path = tmp_path / "candidate.json"
    raw = b'{"items": []}'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        import_draft(path, SOURCE, tmp_path)
    assert (tmp_path / "policies/active.json").read_bytes() == pointer
    assert any(p.read_bytes() == raw for p in (tmp_path / "drafts").glob("*/candidate.json"))


def test_corrupt_or_missing_revision_fails_closed(tmp_path):
    from veriagent.authoring import bootstrap_demo
    from veriagent.policy import load_snapshot
    assert load_snapshot(tmp_path) is None
    bootstrap_demo(tmp_path)
    (tmp_path / "policies/revisions/seed-v1.json").write_text("{}")
    assert load_snapshot(tmp_path) is None


def test_pointer_failure_never_changes_active_revision(tmp_path, monkeypatch):
    import os
    from veriagent.authoring import bootstrap_demo, import_draft, review_candidate, activate_policy
    from veriagent.policy import load_snapshot
    bootstrap_demo(tmp_path)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate()))
    import_draft(path, SOURCE, tmp_path)
    review_candidate(path, tmp_path)
    def fail(*_args):
        raise OSError("simulated atomic pointer failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        activate_policy(path, "v1", home=tmp_path, confirmation="v1")
    assert load_snapshot(tmp_path).revision == "seed-v1"
