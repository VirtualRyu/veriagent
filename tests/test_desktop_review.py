"""Regressions reproduced by the independent whole-change review."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from contextlib import contextmanager

import pytest
from test_desktop_foundation import seed_home, review_external


def test_periodic_status_marks_displayed_old_rules_stale(tmp_path):
    from PySide6.QtWidgets import QApplication
    from veriagent.desktop import MainWindow
    from veriagent.owner import OwnerService
    app = QApplication.instance() or QApplication([])
    seed_home(tmp_path)
    window = MainWindow(OwnerService(tmp_path))
    old_draft = window.draft
    review_external(tmp_path, "other-owner")
    window.refresh_status()
    assert "Stale" in window.draft_label.text()
    assert "seed-v1" in window.rule_basis.text()
    assert "other-owner" in window.state_label.text()
    assert window.draft == old_draft
    window.close()


def test_conflicting_environment_or_argument_cannot_save_lower_priority_selection(tmp_path, monkeypatch):
    from veriagent.state import select_home, resolve_home, app_directory
    a, b = tmp_path / "a", tmp_path / "b"
    seed_home(a)
    seed_home(b)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("VERIAGENT_HOME", str(a))
    with pytest.raises(ValueError, match="override"):
        select_home(b)
    assert resolve_home() == a
    assert not (app_directory() / "state-location.json").exists()
    monkeypatch.delenv("VERIAGENT_HOME")
    with pytest.raises(ValueError, match="override"):
        select_home(b, explicit_override=a)
    assert not (app_directory() / "state-location.json").exists()


def test_invalid_database_selection_retains_previous_valid_selection(tmp_path, monkeypatch):
    from veriagent.state import select_home, resolve_home
    a, b = tmp_path / "a", tmp_path / "b"
    seed_home(a)
    seed_home(b)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("VERIAGENT_HOME", raising=False)
    select_home(a)
    (b / "demo.sqlite3").write_bytes(b"not a SQLite database")
    with pytest.raises(ValueError, match="database"):
        select_home(b)
    assert resolve_home() == a


def test_bootstrap_checks_absence_under_the_publication_lock(tmp_path, monkeypatch):
    import veriagent.authoring as authoring
    from veriagent.models import PolicyBundle
    from veriagent.policy import load_snapshot
    from veriagent.locking import policy_lock as real_lock
    real_publish = authoring.publish_bundle
    owner = PolicyBundle(revision="owner-new", rules=[])
    first_acquisition = True
    @contextmanager
    def interleaved_lock(home):
        nonlocal first_acquisition
        if first_acquisition:
            first_acquisition = False
            # Simulate another owner completing publication just before this writer locks.
            real_publish(owner, home, {"kind": "test-owner"})
        with real_lock(home):
            yield
    monkeypatch.setattr(authoring, "policy_lock", interleaved_lock)
    with pytest.raises(ValueError):
        authoring.bootstrap_demo(tmp_path)
    assert load_snapshot(tmp_path).revision == "owner-new"
    assert not (tmp_path / "policies/revisions/seed-v1.json").exists()
