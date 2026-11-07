from test_desktop_foundation import seed_home


def test_explicit_catalog_initialization_retains_existing_owner_policy(tmp_path):
    from veriagent.owner import OwnerService
    from test_desktop_foundation import review_external
    store = seed_home(tmp_path)
    review_external(tmp_path, "owner-existing")
    with store.transaction() as db:
        db.execute("DELETE FROM approvals")
        db.execute("DELETE FROM documents")
    before = (tmp_path / "policies/active.json").read_bytes()
    owner = OwnerService(tmp_path)
    draft = owner.begin()
    owner.initialize_samples(draft)
    assert (tmp_path / "policies/active.json").read_bytes() == before
    assert len(store.list_documents()) == 3


def test_initialization_does_not_discard_unsaved_owner_work(tmp_path):
    import pytest
    from veriagent.owner import OwnerRule, OwnerService
    from veriagent.models import PolicyRule
    owner = OwnerService(tmp_path)
    draft = owner.begin()
    rule = OwnerRule(title="New rule", rule=PolicyRule(id="R", source_text="Owner rule", effect="BLOCK",
                                                    condition={"op": "atom", "name": "external"}))
    changed = owner.save(draft.model_copy(update={"rules": (rule,)}))
    with pytest.raises(ValueError):
        owner.initialize_samples(changed)
    assert owner.begin().rules == changed.rules
    assert not (tmp_path / "policies/active.json").exists()
