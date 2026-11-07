"""Catch permissive precedence, incomplete facts, and compiler mistranslation."""
import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def seed():
    from veriagent.models import PolicyBundle
    return PolicyBundle.model_validate_json(Path("data/seed_policy.json").read_text())


def direct(node, facts):
    if node["op"] == "atom":
        return facts[node["name"]]
    if node["op"] == "not":
        return not direct(node["child"], facts)
    values = [direct(c, facts) for c in node["children"]]
    return all(values) if node["op"] == "all" else any(values)


def test_sat_agrees_with_independent_truth_table():
    from veriagent.models import ActionContext
    from veriagent.policy import compile_bundle, matches
    bundle = seed()
    compiled = compile_bundle(bundle)
    for bits in itertools.product((False, True), repeat=3):
        facts = dict(zip(("confidential", "external", "approved"), bits))
        context = ActionContext(**facts)
        for rule in compiled.rules:
            assert matches(rule, context) == direct(rule.rule.condition.model_dump(mode="json"), facts)


@pytest.mark.parametrize("bits,want", [
    ((False, False, False), "ALLOW"), ((False, False, True), "ALLOW"),
    ((False, True, False), "HUMAN_REVIEW"), ((False, True, True), "ALLOW"),
    ((True, False, False), "HUMAN_REVIEW"), ((True, False, True), "ALLOW"),
    ((True, True, False), "BLOCK"), ((True, True, True), "BLOCK"),
])
def test_seed_decisions(bits, want):
    from veriagent.models import ActionContext
    from veriagent.policy import compile_bundle, decide
    assert decide(compile_bundle(seed()), ActionContext(confidential=bits[0], external=bits[1], approved=bits[2])).decision == want


def test_block_and_review_take_precedence_over_allow():
    from veriagent.models import ActionContext, PolicyBundle
    from veriagent.policy import compile_bundle, decide
    rules = [{"id": str(i), "source_text": effect, "effect": effect,
              "condition": {"op": "atom", "name": "approved"}}
             for i, effect in enumerate(("ALLOW", "HUMAN_REVIEW", "BLOCK"))]
    context = ActionContext(confidential=False, external=False, approved=True)
    assert decide(compile_bundle(PolicyBundle(revision="test", rules=rules)), context).decision == "BLOCK"
    assert decide(compile_bundle(PolicyBundle(revision="test", rules=rules[:2])), context).decision == "HUMAN_REVIEW"


@pytest.mark.parametrize("condition", [
    {"op": "atom", "name": "manager"}, {"op": "all", "children": []},
    {"op": "any", "children": []}, {"op": "atom", "name": "approved", "extra": True},
    {"op": "eval", "code": "True"},
])
def test_invalid_ast_rejected(condition):
    from veriagent.models import PolicyRule
    with pytest.raises(ValidationError):
        PolicyRule(id="bad", source_text="bad", effect="ALLOW", condition=condition)


def test_limits_duplicate_ids_and_incomplete_context():
    from veriagent.models import ActionContext, PolicyBundle, PolicyRule
    atom = {"op": "atom", "name": "approved"}
    rule = dict(id="x", source_text="approval", effect="ALLOW", condition=atom)
    with pytest.raises(ValidationError):
        PolicyBundle(revision="x", rules=[rule, rule])
    with pytest.raises(ValidationError):
        PolicyBundle(revision="x", rules=[dict(rule, id=str(i)) for i in range(21)])
    with pytest.raises(ValidationError):
        PolicyRule(**dict(rule, condition={"op": "all", "children": [atom] * 50}))
    for _ in range(8):
        atom = {"op": "not", "child": atom}
    with pytest.raises(ValidationError):
        PolicyRule(**dict(rule, condition=atom))
    with pytest.raises(ValidationError):
        ActionContext(confidential=False, external=False)
    with pytest.raises(ValidationError):
        ActionContext(confidential="false", external=False, approved=False)


def test_tautology_and_contradiction():
    from veriagent.models import PolicyBundle, ActionContext
    from veriagent.policy import compile_bundle, decide
    atom = {"op": "atom", "name": "approved"}
    for op, want in [("any", "ALLOW"), ("all", "HUMAN_REVIEW")]:
        rule = dict(id="x", source_text="logic", effect="ALLOW",
                    condition={"op": op, "children": [atom, {"op": "not", "child": atom}]})
        assert decide(compile_bundle(PolicyBundle(revision="x", rules=[rule])),
                      ActionContext(confidential=False, external=False, approved=False)).decision == want
