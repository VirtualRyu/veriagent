"""Owner edits, exact reviewed content, atomic Apply and history contracts."""
import json

import pytest

from test_desktop_foundation import seed_home, review_external


def test_persisted_draft_and_preview_do_not_change_active_policy(tmp_path):
    from veriagent.owner import OwnerService
    store = seed_home(tmp_path)
    owner = OwnerService(tmp_path)
    draft = owner.begin()
    rules = list(draft.rules)
    rules[0] = rules[0].model_copy(update={"enabled": False})
    changed = owner.save(draft.model_copy(update={"rules": tuple(rules)}))
    assert OwnerService(tmp_path).begin() == changed
    preview = owner.preview(changed, "confidential-plan", "manager@outside.test")
    assert preview["active"]["decision"] == "BLOCK"
    assert preview["draft"]["decision"] == "HUMAN_REVIEW"
    assert store.inspect() == {"decisions": [], "outbox": []}
    assert owner.active_revision() == "seed-v1"
    report = owner.review(changed)
    assert any("Disabled" in line for line in report["changes"])
    assert report["warnings"]


def test_apply_checks_stale_windows_and_exact_review(tmp_path):
    from veriagent.owner import OwnerService, StaleDraft
    from veriagent.policy import load_snapshot
    seed_home(tmp_path)
    owner = OwnerService(tmp_path)
    draft = owner.begin()
    saved = owner.save(draft.model_copy(update={"rules": ()}))
    with pytest.raises(StaleDraft):
        owner.save(draft)
    report = owner.review(saved)
    with pytest.raises(ValueError):
        owner.apply(saved, "not-the-reviewed-content")
    assert load_snapshot(tmp_path).revision == "seed-v1"
    applied = owner.apply(saved, report["token"])
    assert load_snapshot(tmp_path).revision == applied
    assert load_snapshot(tmp_path).rules == ()
    assert owner.preview(owner.begin(), "public-guide", "alice@example.org")["active"]["decision"] == "HUMAN_REVIEW"
    with pytest.raises(StaleDraft):
        owner.apply(saved, report["token"])


def test_external_activation_leaves_stale_draft_visible(tmp_path):
    from veriagent.owner import OwnerService, StaleDraft
    seed_home(tmp_path)
    owner = OwnerService(tmp_path)
    draft = owner.begin()
    report = owner.review(draft)
    review_external(tmp_path)
    with pytest.raises(StaleDraft):
        owner.apply(draft, report["token"])
    assert owner.begin().base_revision == "seed-v1"
    assert owner.active_revision() == "updated"


def test_failed_apply_preserves_pointer_and_restore_creates_new_revision(tmp_path, monkeypatch):
    import os
    from veriagent.owner import OwnerService
    seed_home(tmp_path)
    owner = OwnerService(tmp_path)
    draft = owner.begin()
    changed = owner.save(draft.model_copy(update={"rules": ()}))
    report = owner.review(changed)
    pointer = (tmp_path / "policies/active.json").read_bytes()
    original_replace = os.replace
    def fail_pointer(source, destination):
        if str(destination).endswith("active.json"):
            raise OSError("simulated pointer failure")
        original_replace(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_pointer)
        with pytest.raises(OSError):
            owner.apply(changed, report["token"])
    assert (tmp_path / "policies/active.json").read_bytes() == pointer
    rev = owner.apply(changed, report["token"])
    original = (tmp_path / "policies/revisions/seed-v1.json").read_bytes()
    restored = owner.restore(owner.begin(), "seed-v1")
    assert owner.active_revision() == rev
    new_rev = owner.apply(restored, owner.review(restored)["token"])
    assert new_rev not in (rev, "seed-v1")
    assert (tmp_path / "policies/revisions/seed-v1.json").read_bytes() == original
    assert len(owner.history()) >= 3


def test_rendered_nested_rule_and_actual_sat_input_agree(tmp_path):
    import itertools
    from veriagent.models import ActionContext, PolicyRule, PolicyBundle
    from veriagent.policy import compile_bundle, matches
    from veriagent.rendering import artifacts
    rule = PolicyRule(id="N", source_text="Original wording", effect="BLOCK", condition={"op": "not", "child": {
        "op": "any", "children": [{"op": "atom", "name": "approved"}, {"op": "all", "children": [
            {"op": "atom", "name": "external"}, {"op": "not", "child": {"op": "atom", "name": "confidential"}}]}]}})
    compiled = compile_bundle(PolicyBundle(revision="nested", rules=[rule])).rules[0]
    for c, e, a in itertools.product((False, True), repeat=3):
        ctx = ActionContext(confidential=c, external=e, approved=a)
        representation = artifacts(compiled, ctx)
        assert representation["clauses"] == [list(clause) for clause in compiled.clauses]
        assert representation["matched"] == (not (a or (e and not c))) == matches(compiled, ctx)
        assert representation["effect"] == "BLOCK"
        assert representation["logic"] == "NOT ((approved OR (external AND NOT (confidential))))"
