"""The documented module uses real MCP and isolated synthetic state."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_documented_demo_uses_temporary_state(tmp_path):
    sentinel = tmp_path / "owner-marker.txt"
    sentinel.write_text("preserve")
    environment = {**os.environ, "VERIAGENT_HOME": str(tmp_path)}
    result = subprocess.run([sys.executable, "-m", "examples.demo"],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert [row["decision"] for row in report["requests"]] == ["ALLOW", "BLOCK", "HUMAN_REVIEW"]
    assert [row["executed"] for row in report["requests"]] == [True, False, False]
    assert all(row["policy_revision"] == "seed-v1" for row in report["requests"])
    assert report["outbox_rows"] == 1
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_text() == "preserve"
