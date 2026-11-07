"""One explicit owner state directory for desktop, CLI and MCP processes."""
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def app_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "VeriAgent"


def resolve_home(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("VERIAGENT_HOME"):
        return Path(os.environ["VERIAGENT_HOME"]).expanduser().resolve()
    location = app_directory() / "state-location.json"
    if location.exists():
        return Path(json.loads(location.read_text(encoding="utf-8"))["home"]).expanduser().resolve()
    return app_directory() / "state"


def select_home(home: Path, *, explicit_override=None):
    from .authoring import atomic_json
    from .policy import load_snapshot
    home = home.expanduser().resolve()
    override = explicit_override or os.environ.get("VERIAGENT_HOME")
    if override and Path(override).expanduser().resolve() != home:
        raise ValueError("This session has an explicit state override. Reopen the installed companion without --home/VERIAGENT_HOME before selecting a different persistent state.")
    if not (home / "demo.sqlite3").is_file() or load_snapshot(home) is None:
        raise ValueError("Select an existing VeriAgent state directory with a valid policy and database.")
    required = {"documents": {"id", "name", "classification"}, "approvals": {"document_id", "recipient"},
                "decisions": {"request_id", "actor", "document_id", "recipient", "decision", "result_json", "created_at"},
                "outbox": {"id", "request_id", "document_id", "recipient", "created_at"}}
    try:
        with closing(sqlite3.connect((home / "demo.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Owner database failed its integrity check")
            for table, expected in required.items():
                if not expected.issubset({row[1] for row in db.execute(f"PRAGMA table_info({table})")}):
                    raise ValueError(f"Owner database lacks the expected {table} schema")
    except sqlite3.Error as exc:
        raise ValueError(f"Owner database cannot be opened: {exc}") from exc
    from .owner import OwnerDraft, OwnerRule
    draft = home / "owner/draft.json"
    if draft.exists():
        OwnerDraft.model_validate_json(draft.read_bytes())
    snapshot = load_snapshot(home)
    metadata = home / "policies/revisions" / f"{snapshot.revision}.provenance.json"
    if metadata.exists():
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if "owner_rules" in record:
            rules = [OwnerRule.model_validate(item) for item in record["owner_rules"]]
            if tuple(item.rule for item in rules if item.enabled) != tuple(item.rule for item in snapshot.rules):
                raise ValueError("Owner metadata does not match the active policy")
    atomic_json(app_directory() / "state-location.json", {"home": str(home)})


def resource_path(relative: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / relative
