"""Trusted context construction and the single guarded action path."""
import re
import sqlite3
from uuid import uuid4

from .models import ActionContext, ActionResult, Decision
from .policy import CompiledBundle, decide, load_snapshot
from .rendering import artifacts
from .store import Store

LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*", re.ASCII)
DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", re.ASCII)


class PersistenceError(RuntimeError):
    """No recorded success can be claimed when the transaction fails."""


def normalize_recipient(recipient: str) -> str:
    if not isinstance(recipient, str) or len(recipient) > 254 or recipient.count("@") != 1:
        raise ValueError("one simple email address required")
    local, domain = recipient.split("@")
    labels = domain.split(".")
    if len(local) > 64 or not LOCAL.fullmatch(local) or len(labels) < 2 or not all(DOMAIN_LABEL.fullmatch(x) for x in labels):
        raise ValueError("invalid simple email address")
    return local + "@" + domain.lower()


def evaluate_request(db, bundle, document_id, recipient):
    """Read trusted facts without writes; shared by previews and execution."""
    normalized, context = recipient, None
    decision = Decision(decision="HUMAN_REVIEW", reason="Required metadata or active policy is unavailable.")
    try:
        normalized = normalize_recipient(recipient)
    except ValueError:
        decision = Decision(decision="HUMAN_REVIEW", reason="Recipient is invalid; one simple email address is required.")
    else:
        doc = db.execute("SELECT classification FROM documents WHERE id=?", (document_id,)).fetchone()
        if doc is not None and doc["classification"] in ("public", "confidential"):
            approved = db.execute("SELECT 1 FROM approvals WHERE document_id=? AND recipient=?", (document_id, normalized)).fetchone() is not None
            context = ActionContext(confidential=doc["classification"] == "confidential",
                                    external=normalized.split("@")[1] != "example.org", approved=approved)
            if bundle is not None:
                decision = decide(bundle, context)
    trace = {"revision": bundle.revision if bundle else None,
             "rules": [artifacts(rule, context) for rule in bundle.rules] if bundle is not None and context is not None else []}
    return decision, normalized, context, trace


class SharingService:
    def __init__(self, store: Store, bundle: CompiledBundle | None = None, *, home=None):
        self.store, self.bundle, self.home = store, bundle, home

    def snapshot(self):
        return load_snapshot(self.home) if self.home is not None else self.bundle

    def list_documents(self):
        return self.store.list_documents()

    def preview(self, document_id, recipient, *, bundle=None):
        from contextlib import closing
        snapshot = bundle if bundle is not None else self.snapshot()
        with closing(self.store.connect()) as db:
            db.execute("BEGIN")
            decision, normalized, context, trace = evaluate_request(db, snapshot, document_id, recipient)
        return {**decision.model_dump(mode="json"), "policy_revision": snapshot.revision if snapshot else None,
                "recipient": normalized, "facts": context.model_dump() if context else None, "trace": trace,
                "preview": True, "executed": False}

    def share_document(self, document_id: str, recipient: str) -> ActionResult:
        request_id = str(uuid4())
        snapshot = self.snapshot()
        try:
            with self.store.transaction() as db:
                decision, normalized, context, trace = evaluate_request(db, snapshot, document_id, recipient)
                executed = decision.decision == "ALLOW"
                result = ActionResult(**decision.model_dump(), request_id=request_id, executed=executed,
                                      policy_revision=snapshot.revision if snapshot else None,
                                      outbox_id=str(uuid4()) if executed else None)
                self.store.record(db, result, document_id, normalized, facts=context.model_dump() if context else None, trace=trace)
            return result
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            raise PersistenceError(f"Request {request_id} failed; no committed success is reported.") from exc
