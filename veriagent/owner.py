"""Desktop owner application logic. Drafts never enter the MCP tool surface."""
import hashlib
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import Field, StrictBool, model_validator

from .authoring import atomic_json, _publish_bundle_unlocked
from .locking import policy_lock
from .models import StrictModel, PolicyRule, PolicyBundle, ActionContext
from .policy import FACTS, compile_bundle, decide, load_snapshot
from .service import SharingService
from .store import Store


def now():
    return datetime.now(timezone.utc).isoformat()


class StaleDraft(ValueError):
    pass


class OwnerRule(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    enabled: StrictBool = True
    rule: PolicyRule
    original_source: str | None = None
    origin: str = "imported or legacy"
    edited_at: str | None = None


class OwnerDraft(StrictModel):
    base_pointer: str | None
    base_revision: str | None
    version: str
    rules: tuple[OwnerRule, ...] = Field(default=(), max_length=20)
    restored_from: str | None = None

    @model_validator(mode="after")
    def validate_rules(self):
        if len({item.rule.id for item in self.rules}) != len(self.rules):
            raise ValueError("Duplicate rule IDs")
        return self

    def bundle(self, revision="owner-draft"):
        return PolicyBundle(revision=revision, rules=[item.rule for item in self.rules if item.enabled])


class OwnerService:
    def __init__(self, home: Path):
        self.home = Path(home).resolve()
        self.path = self.home / "owner/draft.json"
        self.store = Store(self.home / "demo.sqlite3")
        self.store.initialize()
        self.sharing = SharingService(self.store, home=self.home)

    def pointer(self):
        path = self.home / "policies/active.json"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def active_revision(self):
        snapshot = load_snapshot(self.home)
        return snapshot.revision if snapshot else None

    def revision_rules(self, revision):
        bundle = PolicyBundle.model_validate_json((self.home / "policies/revisions" / f"{revision}.json").read_bytes())
        path = self.home / "policies/revisions" / f"{revision}.provenance.json"
        metadata = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if "owner_rules" in metadata:
            rules = tuple(OwnerRule.model_validate(item) for item in metadata["owner_rules"])
            if tuple(item.rule for item in rules if item.enabled) != bundle.rules:
                raise ValueError("Owner metadata does not match the executable revision")
        else:
            rules = tuple(OwnerRule(title=rule.id, rule=rule, original_source=rule.source_text) for rule in bundle.rules)
        return rules

    def baseline(self, version=None):
        revision = self.active_revision()
        return OwnerDraft(base_pointer=self.pointer(), base_revision=revision, version=version or uuid4().hex,
                          rules=self.revision_rules(revision) if revision else ())

    def begin(self):
        with policy_lock(self.home):
            if self.path.exists():
                draft = OwnerDraft.model_validate_json(self.path.read_bytes())
                revision = self.active_revision()
                provenance = self.home / "policies/revisions" / f"{revision}.provenance.json"
                metadata = json.loads(provenance.read_text(encoding="utf-8")) if provenance.exists() else {}
                if draft.base_pointer != self.pointer() and metadata.get("applied_draft_version") == draft.version:
                    return self.baseline(draft.version)
                return draft
            draft = self.baseline()
            atomic_json(self.path, draft.model_dump(mode="json"))
            return draft

    def check(self, draft):
        if self.pointer() != draft.base_pointer:
            raise StaleDraft("Active policy changed. Reload and review the latest policy before editing or applying.")
        if self.path.exists():
            current = OwnerDraft.model_validate_json(self.path.read_bytes())
            if current.version != draft.version:
                raise StaleDraft("Another owner window changed the draft. Reload it before editing or applying.")

    def save(self, draft):
        draft = OwnerDraft.model_validate(draft.model_dump(mode="json"))
        with policy_lock(self.home):
            self.check(draft)
            updated = draft.model_copy(update={"version": uuid4().hex})
            atomic_json(self.path, updated.model_dump(mode="json"))
            return updated

    def reload_active(self, draft):
        with policy_lock(self.home):
            if self.path.exists() and OwnerDraft.model_validate_json(self.path.read_bytes()).version != draft.version:
                raise StaleDraft("Another owner window changed the draft. Reopen before discarding anything.")
            baseline = self.baseline()
            atomic_json(self.path, baseline.model_dump(mode="json"))
            return baseline

    @staticmethod
    def token(draft):
        return hashlib.sha256(draft.model_dump_json().encode("utf-8")).hexdigest()

    def review(self, draft):
        self.check(draft)
        proposed = compile_bundle(draft.bundle())
        active = load_snapshot(self.home)
        old = {r.rule.id: r for r in self.revision_rules(active.revision)} if active else {}
        new = {r.rule.id: r for r in draft.rules}
        changes, warnings = [], []
        for key in sorted(old.keys() | new.keys()):
            before, after = old.get(key), new.get(key)
            if before == after:
                continue
            if after is None:
                label = "Deleted"
            elif before is None:
                label = "Added"
            elif before.enabled and not after.enabled:
                label = "Disabled"
            elif not before.enabled and after.enabled:
                label = "Enabled"
            else:
                label = "Modified"
            changes.append(f"{label}: {key} — {(after or before).title}")
            if before and before.enabled and before.rule.effect in ("BLOCK", "HUMAN_REVIEW") and (
                    after is None or not after.enabled or after.rule != before.rule):
                warnings.append(f"Protection removed or changed: {key} ({before.rule.effect})")
        if not proposed.rules:
            warnings.append("No enabled rules: every valid request requires HUMAN_REVIEW; this is not allow-all.")
        if draft.restored_from:
            warnings.append(f"Restores {draft.restored_from} as a new revision; original history is retained.")
        truth_table = []
        for bits in itertools.product((False, True), repeat=3):
            context = ActionContext(**dict(zip(FACTS, bits)))
            truth_table.append({"facts": context.model_dump(), "active": decide(active, context).decision if active else "HUMAN_REVIEW",
                                "draft": decide(proposed, context).decision})
        return {"token": self.token(draft), "base_revision": draft.base_revision, "changes": changes,
                "warnings": warnings, "truth_table": truth_table}

    def apply(self, draft, review_token):
        draft = OwnerDraft.model_validate(draft.model_dump(mode="json"))
        if review_token != self.token(draft):
            raise ValueError("Review the exact proposed changes before Apply")
        with policy_lock(self.home):
            self.check(draft)
            revision = "desktop-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
            bundle = draft.bundle(revision)
            compile_bundle(bundle)
            report = self.review(draft)
            _publish_bundle_unlocked(bundle, self.home, {"kind": "owner-visual", "created_at": now(),
                "base_revision": draft.base_revision, "restored_from": draft.restored_from,
                "applied_draft_version": draft.version, "owner_rules": [r.model_dump(mode="json") for r in draft.rules],
                "summary": report["changes"], "warnings": report["warnings"]})
            return revision

    def preview(self, draft, document_id, recipient):
        return {"active": self.sharing.preview(document_id, recipient),
                "draft": self.sharing.preview(document_id, recipient, bundle=compile_bundle(draft.bundle()))}

    def initialize_samples(self, draft):
        with policy_lock(self.home):
            self.check(draft)
            if self.store.list_documents():
                raise ValueError("Catalog already exists; samples never replace existing documents or approvals.")
            if draft.rules != self.baseline().rules or draft.restored_from:
                raise ValueError("Apply or explicitly discard draft changes before initializing samples.")
            if self.active_revision() is None:
                from .authoring import _bootstrap_demo_unlocked
                _bootstrap_demo_unlocked(self.home)
                self.store.initialize(seed_demo=True)
                baseline = self.baseline()
                atomic_json(self.path, baseline.model_dump(mode="json"))
                return baseline
            self.store.initialize(seed_demo=True)
            return draft

    def history(self):
        directory = self.home / "policies/revisions"
        rows = []
        for path in directory.glob("*.json"):
            if path.name.endswith(".provenance.json"):
                continue
            try:
                bundle = PolicyBundle.model_validate_json(path.read_bytes())
                metadata_path = path.with_name(f"{bundle.revision}.provenance.json")
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
                rows.append({"revision": bundle.revision, "time": metadata.get("created_at", "Original time not recorded"),
                             "summary": metadata.get("summary", [metadata.get("kind", "Original summary not recorded")]),
                             "active": bundle.revision == self.active_revision()})
            except (ValueError, OSError):
                continue
        return sorted(rows, key=lambda row: row["revision"], reverse=True)

    def restore(self, draft, revision):
        if revision not in {item["revision"] for item in self.history()}:
            raise ValueError("Choose a valid existing revision")
        return self.save(draft.model_copy(update={"rules": self.revision_rules(revision), "restored_from": revision}))
