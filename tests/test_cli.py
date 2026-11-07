"""Exercise the owner CLI as a process, including refusal of activation."""
import json
import os
import subprocess
import sys

from test_authoring import SOURCE, candidate


def run_cli(home, *args, input=None):
    return subprocess.run([sys.executable, "-m", "veriagent.cli", "--home", str(home), *args],
                          input=input, text=True, capture_output=True, timeout=15)


def test_owner_cli_import_review_and_declined_activation(tmp_path):
    home = tmp_path / "state"
    assert run_cli(home, "init-demo").returncode == 0
    before = (home / "policies/active.json").read_bytes()
    raw = tmp_path / "candidate.json"
    raw.write_text(json.dumps(candidate()))
    source = tmp_path / "source.txt"
    source.write_text(SOURCE)
    assert run_cli(home, "import-draft", "--input", str(raw), "--source", str(source)).returncode == 0
    reviewed = run_cli(home, "review", "--candidate", str(raw))
    assert reviewed.returncode == 0
    assert "A1: ALLOW" in reviewed.stdout
    declined = run_cli(home, "activate", "--candidate", str(raw), "--revision", "v2", input="no\n")
    assert declined.returncode != 0
    assert (home / "policies/active.json").read_bytes() == before
    assert json.loads(run_cli(home, "logs").stdout) == {"decisions": [], "outbox": []}


def test_reset_requires_literal_confirmation_and_preserves_policy(tmp_path):
    from veriagent.service import SharingService
    from veriagent.store import Store
    from veriagent.policy import load_snapshot
    home = tmp_path / "state"
    assert run_cli(home, "init-demo").returncode == 0
    store = Store(home / "demo.sqlite3")
    SharingService(store, load_snapshot(home)).share_document("public-guide", "alice@example.org")
    assert run_cli(home, "reset-demo", input="no\n").returncode != 0
    assert len(json.loads(run_cli(home, "logs").stdout)["outbox"]) == 1
    assert run_cli(home, "reset-demo", input="RESET-DEMO\n").returncode == 0
    assert json.loads(run_cli(home, "logs").stdout)["outbox"] == []
    assert load_snapshot(home).revision == "seed-v1"
