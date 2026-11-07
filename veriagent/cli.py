"""Owner terminal commands. Never advertise these commands as agent tools."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .authoring import ROOT, activate_policy, bootstrap_demo, import_draft, review_candidate
from .models import PolicyDraft
from .store import APPROVALS, DOCUMENTS, Store
from .state import resolve_home


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-demo", help="Initialize synthetic catalog and the plan's seed policies once")
    commands.add_parser("logs", help="Inspect audit and simulated outbox")
    commands.add_parser("schema", help="Print the strict assistant candidate JSON schema")
    commands.add_parser("reset-demo", help="Clear only simulated records/catalog; retain policy history")
    importer = commands.add_parser("import-draft")
    importer.add_argument("--input", required=True, type=Path)
    importer.add_argument("--source", required=True, type=Path)
    review = commands.add_parser("review")
    review.add_argument("--candidate", required=True, type=Path)
    activate = commands.add_parser("activate")
    activate.add_argument("--candidate", required=True, type=Path)
    activate.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    args.home = resolve_home(args.home)
    store = Store(args.home / "demo.sqlite3")
    try:
        if args.command == "schema":
            print(json.dumps(PolicyDraft.model_json_schema(), indent=2))
        elif args.command == "init-demo":
            artifact = bootstrap_demo(args.home)
            store.initialize(seed_demo=True)
            print(f"Synthetic seed fixture installed: {artifact}. Updated MCP servers use it on the next action.")
        elif args.command == "import-draft":
            draft = import_draft(args.input, args.source.read_text(encoding="utf-8"), args.home)
            print(f"Imported; accepted={draft.accepted}. Active policy unchanged. Run review next.")
            return 0 if draft.accepted else 2
        elif args.command == "review":
            print(review_candidate(args.candidate, args.home))
        elif args.command == "activate":
            print(review_candidate(args.candidate, args.home))
            confirmation = input(f"Owner only: type revision {args.revision!r} to activate (anything else cancels): ")
            artifact = activate_policy(args.candidate, args.revision, home=args.home, confirmation=confirmation)
            print(f"Activated {artifact}. Updated MCP servers read it on the next action; in-flight requests keep their pinned revision.")
        elif args.command == "logs":
            print(json.dumps(store.inspect(), indent=2))
        elif args.command == "reset-demo":
            confirmation = input("Delete synthetic audit/outbox and restore demo catalog? Type RESET-DEMO: ")
            if confirmation != "RESET-DEMO":
                raise ValueError("Reset cancelled")
            with store.transaction() as db:
                for table in ("outbox", "decisions", "approvals", "documents"):
                    db.execute(f"DELETE FROM {table}")
                db.executemany("INSERT INTO documents VALUES (?, ?, ?)", DOCUMENTS)
                db.executemany("INSERT INTO approvals VALUES (?, ?)", APPROVALS)
            print("Synthetic records reset. Policy revisions and drafts retained.")
        return 0
    except (ValueError, OSError, sqlite3.Error, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
