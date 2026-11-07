"""Owner-only import/review/activation. None of these functions is an MCP tool."""
import hashlib
import itertools
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from .models import ActionContext, All, Atom, Not, PolicyBundle, PolicyDraft
from .policy import FACTS, compile_bundle, matches
from .locking import policy_lock

ROOT = Path(__file__).resolve().parent.parent


def parse_source(source_text: str) -> dict[str, str]:
    sentences = {}
    for line in source_text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([1-9][0-9]*)\. (.+)", line)
        if match is None or match[1] in sentences:
            raise ValueError("Use unique numbered source lines: 1. Exact sentence")
        sentences[match[1]] = match[2]
    if not 1 <= len(sentences) <= 20:
        raise ValueError("Source requires 1–20 numbered sentences")
    return sentences


def import_policy(candidate: dict, source_text: str) -> PolicyDraft:
    draft = PolicyDraft.model_validate(candidate)
    expected = parse_source(source_text)
    actual = {item.source_id: item.source_text for item in draft.items}
    if actual != expected:
        raise ValueError("Candidate must cover every source ID with identical text")
    return draft


def read_json(raw: bytes):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    if len(raw) > 200_000:
        raise ValueError("Candidate exceeds 200 KB")
    try:
        return json.loads(raw, object_pairs_hook=no_duplicates)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid or excessively nested JSON") from exc


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def import_draft(candidate_path: Path, source_text: str, home: Path) -> PolicyDraft:
    raw = candidate_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    record = home / "drafts" / digest
    record.mkdir(parents=True, exist_ok=True)
    if not (record / "candidate.json").exists():
        (record / "candidate.json").write_bytes(raw)
        (record / "source.txt").write_text(source_text, encoding="utf-8")
    elif (record / "source.txt").read_text(encoding="utf-8") != source_text:
        raise ValueError("This raw candidate already has a different preserved source")
    try:
        draft = import_policy(read_json(raw), source_text)
    except ValueError as exc:
        atomic_json(record / "validation.json", {"accepted": False, "error": str(exc), "sha256": digest})
        raise
    atomic_json(record / "validation.json", {"accepted": draft.accepted, "sha256": digest,
                                             "unresolved": [item.source_id for item in draft.items if item.status != "supported"]})
    return draft


def imported(candidate_path: Path, home: Path):
    raw = candidate_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    record = home / "drafts" / digest
    try:
        if (record / "candidate.json").read_bytes() != raw:
            raise ValueError("Preserved raw candidate does not match")
        draft = import_policy(read_json(raw), (record / "source.txt").read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("Import this exact candidate before review/activation") from exc
    return draft, record, digest


def controlled_english(node):
    if isinstance(node, Atom):
        return {"confidential": "document is confidential", "external": "recipient is external",
                "approved": "exact pair has recorded approval"}[node.name]
    if isinstance(node, Not):
        return f"NOT ({controlled_english(node.child)})"
    joiner = " AND " if isinstance(node, All) else " OR "
    return "(" + joiner.join(controlled_english(c) for c in node.children) + ")"


def render_review(draft: PolicyDraft) -> str:
    lines = ["Candidate review — schema validation does not establish semantic correctness."]
    for item in draft.items:
        lines.extend([f"\n{item.source_id}. {item.source_text}", f"Status: {item.status}"])
        if item.rule is None:
            lines.append(f"Unresolved: {item.explanation}")
            continue
        rule = item.rule
        compiled = compile_bundle(PolicyBundle(revision="review", rules=[rule])).rules[0]
        lines.extend([f"{rule.id}: {rule.effect} WHEN {controlled_english(rule.condition)}",
                      "AST: " + rule.condition.model_dump_json()])
        for bits in itertools.product((False, True), repeat=3):
            facts = dict(zip(FACTS, bits))
            label = ", ".join(f"{key}={value}" for key, value in facts.items())
            lines.append(f"  {label}: matches={matches(compiled, ActionContext(**facts))}")
    lines.append("\nNo policy changed. Owner must check meaning and explicitly activate; updated MCP servers refresh on the next action.")
    return "\n".join(lines)


def review_candidate(candidate_path: Path, home: Path) -> str:
    draft, record, digest = imported(candidate_path, home)
    result = render_review(draft)
    atomic_json(record / "review.json", {"sha256": digest, "review_displayed": True})
    return result


def publish_bundle(bundle: PolicyBundle, home: Path, provenance: dict) -> Path:
    with policy_lock(home):
        return _publish_bundle_unlocked(bundle, home, provenance)


def _publish_bundle_unlocked(bundle: PolicyBundle, home: Path, provenance: dict) -> Path:
    policies = home / "policies"
    revisions = policies / "revisions"
    revisions.mkdir(parents=True, exist_ok=True)
    artifact = revisions / f"{bundle.revision}.json"
    content = (bundle.model_dump_json(indent=2) + "\n").encode("utf-8")
    # Exclusive creation: a revision name can never overwrite a previous artifact.
    with artifact.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_json(revisions / f"{bundle.revision}.provenance.json", provenance)
    atomic_json(policies / "active.json", {"revision": bundle.revision, "sha256": hashlib.sha256(content).hexdigest()})
    return artifact


def activate_policy(candidate_path: Path, revision: str, *, home: Path, confirmation: str) -> Path:
    draft, record, digest = imported(candidate_path, home)
    if not draft.accepted:
        raise ValueError("Cannot activate unresolved sentences")
    try:
        reviewed = read_json((record / "review.json").read_bytes())
    except OSError as exc:
        raise ValueError("Display review for this exact candidate before activation") from exc
    if reviewed.get("sha256") != digest or not reviewed.get("review_displayed"):
        raise ValueError("Review record does not match candidate")
    bundle = PolicyBundle(revision=revision, rules=[item.rule for item in draft.items])
    if confirmation != revision:
        raise ValueError("Owner confirmation must equal the requested revision")
    compile_bundle(bundle)
    return publish_bundle(bundle, home, {"kind": "owner-confirmed-import", "candidate_sha256": digest,
                                          "source": str(record / "source.txt")})


def bootstrap_demo(home: Path) -> Path:
    """Install only the plan's explicitly specified synthetic seed fixture."""
    with policy_lock(home):
        return _bootstrap_demo_unlocked(home)


def _bootstrap_demo_unlocked(home: Path) -> Path:
    if (home / "policies/active.json").exists() or (home / "policies/revisions/seed-v1.json").exists():
        raise ValueError("Demo policies already initialized; bootstrap never replaces active policy")
    from .state import resource_path
    bundle = PolicyBundle.model_validate_json(resource_path("data/seed_policy.json").read_bytes())
    return _publish_bundle_unlocked(bundle, home, {"kind": "synthetic-plan-fixture", "source": "data/seed_policy.json"})
