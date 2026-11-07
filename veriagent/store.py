"""Small SQLite store; a decision and its simulated outbox commit together."""
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from .models import DocumentSummary

DOCUMENTS = (
    ("public-guide", "Public guide", "public"),
    ("confidential-plan", "Confidential plan", "confidential"),
    ("unknown-label", "Unclassified example", None),
)
APPROVALS = (
    ("confidential-plan", "manager@example.org"),
    ("confidential-plan", "manager@outside.test"),
    ("public-guide", "manager@example.org"),
    ("public-guide", "manager@outside.test"),
)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def initialize(self, *, seed_demo: bool = False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, name TEXT NOT NULL, classification TEXT);
                CREATE TABLE IF NOT EXISTS approvals(document_id TEXT NOT NULL REFERENCES documents(id), recipient TEXT NOT NULL,
                    PRIMARY KEY(document_id, recipient));
                CREATE TABLE IF NOT EXISTS decisions(request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, document_id TEXT NOT NULL,
                    recipient TEXT NOT NULL, decision TEXT NOT NULL, result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES decisions(request_id),
                    document_id TEXT NOT NULL, recipient TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(decisions)")}
            for name in ("facts_json", "trace_json"):
                if name not in columns:
                    db.execute(f"ALTER TABLE decisions ADD COLUMN {name} TEXT")
            # Ordinary server startup must never invent missing trusted metadata.
            # Seeding is an explicit owner CLI operation only.
            if seed_demo and db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0:
                db.executemany("INSERT INTO documents VALUES (?, ?, ?)", DOCUMENTS)
                db.executemany("INSERT INTO approvals VALUES (?, ?)", APPROVALS)

    def commit(self, db):
        db.commit()

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                self.commit(db)
            except BaseException:
                db.rollback()
                raise

    def list_documents(self):
        with closing(self.connect()) as db:
            return [DocumentSummary(**dict(row)) for row in db.execute("SELECT id, name, classification FROM documents ORDER BY id")]

    def record(self, db, result, document_id, recipient, *, facts=None, trace=None):
        db.execute("INSERT INTO decisions(request_id, actor, document_id, recipient, decision, result_json, facts_json, trace_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (result.request_id, "demo-owner", document_id, recipient, result.decision, result.model_dump_json(exclude_none=True),
                    json.dumps(facts) if facts is not None else None, json.dumps(trace) if trace is not None else None))
        if result.executed:
            db.execute("INSERT INTO outbox(id, request_id, document_id, recipient) VALUES (?, ?, ?, ?)",
                       (result.outbox_id, result.request_id, document_id, recipient))

    def inspect(self):
        with closing(self.connect()) as db:
            return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("decisions", "outbox")}

    def activity(self, limit=200):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT * FROM decisions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.update(json.loads(item["result_json"]))
            item["facts"] = json.loads(item["facts_json"]) if item.get("facts_json") else None
            item["trace"] = json.loads(item["trace_json"]) if item.get("trace_json") else None
            result.append(item)
        return result
