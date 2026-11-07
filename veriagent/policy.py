"""AST → SymPy CNF → SAT under complete trusted fact assignments."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from pysat.solvers import Solver
from sympy import And, Not as SNot, Or, Symbol, false, true
from sympy.logic.boolalg import to_cnf

from .models import ActionContext, All, AnyOf, Atom, Decision, Not, PolicyBundle, PolicyRule

FACTS = ("confidential", "external", "approved")
SYMBOLS = {name: Symbol(name) for name in FACTS}
VARIABLES = {name: i + 1 for i, name in enumerate(FACTS)}


@dataclass(frozen=True)
class CompiledRule:
    rule: PolicyRule
    clauses: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class CompiledBundle:
    revision: str
    rules: tuple[CompiledRule, ...]


def symbolic(node):
    if isinstance(node, Atom):
        return SYMBOLS[node.name]
    if isinstance(node, Not):
        return SNot(symbolic(node.child))
    return (And if isinstance(node, All) else Or)(*(symbolic(c) for c in node.children))


def compile_bundle(bundle: PolicyBundle) -> CompiledBundle:
    compiled = []
    for rule in bundle.rules:
        cnf = to_cnf(symbolic(rule.condition), simplify=True)
        if cnf == true:
            clauses = ()
        elif cnf == false:
            clauses = ((),)
        else:
            terms = cnf.args if cnf.func == And else (cnf,)
            def literal(term):
                return -VARIABLES[str(term.args[0])] if term.func == SNot else VARIABLES[str(term)]
            clauses = tuple(tuple(literal(t) for t in (term.args if term.func == Or else (term,))) for term in terms)
        compiled.append(CompiledRule(rule, clauses))
    return CompiledBundle(bundle.revision, tuple(compiled))


def matches(rule: CompiledRule, context: ActionContext) -> bool:
    assignments = [VARIABLES[name] if getattr(context, name) else -VARIABLES[name] for name in FACTS]
    with Solver(name="m22", bootstrap_with=rule.clauses) as solver:
        return solver.solve(assumptions=assignments)


def decide(bundle: CompiledBundle, context: ActionContext) -> Decision:
    matching = [rule.rule for rule in bundle.rules if matches(rule, context)]
    ids = tuple(rule.id for rule in matching)
    for effect in ("BLOCK", "HUMAN_REVIEW", "ALLOW"):
        winners = [rule.id for rule in matching if rule.effect == effect]
        if winners:
            return Decision(decision=effect, matched_policy_ids=ids,
                            reason=f"{effect} by {', '.join(winners)}; BLOCK > HUMAN_REVIEW > ALLOW.")
    return Decision(decision="HUMAN_REVIEW", reason="No matching permission; owner review required.")


def load_snapshot(home: Path) -> CompiledBundle | None:
    """Resolve an immutable snapshot; callers pin it for one request."""
    try:
        pointer = json.loads((home / "policies/active.json").read_text(encoding="utf-8"))
        revision = pointer["revision"]
        import re
        if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", revision):
            return None
        content = (home / "policies/revisions" / f"{revision}.json").read_bytes()
        if hashlib.sha256(content).hexdigest() != pointer["sha256"]:
            return None
        bundle = PolicyBundle.model_validate_json(content)
        return compile_bundle(bundle) if bundle.revision == revision else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
