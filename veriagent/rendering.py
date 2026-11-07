"""Deterministic views from canonical AST and actual compiler clauses."""
from .authoring import controlled_english
from .models import All, Atom, Not
from .policy import FACTS, VARIABLES, matches


def logic(node):
    if isinstance(node, Atom):
        return node.name
    if isinstance(node, Not):
        return f"NOT ({logic(node.child)})"
    return "(" + (" AND " if isinstance(node, All) else " OR ").join(logic(c) for c in node.children) + ")"


def dimacs(clauses):
    return f"p cnf 3 {len(clauses)}\n" + "".join(" ".join(map(str, clause)) + (" " if clause else "") + "0\n" for clause in clauses)


def artifacts(compiled, context=None):
    clauses = [list(c) for c in compiled.clauses]
    inverse = {v: k for k, v in VARIABLES.items()}
    cnf = "TRUE" if not clauses else " AND ".join(
        "FALSE" if not clause else "(" + " OR ".join(("NOT " if n < 0 else "") + inverse[abs(n)] for n in clause) + ")"
        for clause in clauses)
    data = {"rule_id": compiled.rule.id, "effect": compiled.rule.effect,
            "logic": logic(compiled.rule.condition), "english": controlled_english(compiled.rule.condition),
            "cnf": cnf, "variables": dict(VARIABLES), "auxiliary_variables": [],
            "clauses": clauses, "dimacs": dimacs(clauses)}
    if context is not None:
        assumptions = [VARIABLES[n] if getattr(context, n) else -VARIABLES[n] for n in FACTS]
        data["query"] = {"assumptions": assumptions, "clauses_with_facts": clauses + [[n] for n in assumptions],
                         "dimacs_with_facts": dimacs(clauses + [[n] for n in assumptions])}
        data["matched"] = matches(compiled, context)
    return data
