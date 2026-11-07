"""One widget contract: preserve nested ASTs through the visual editor."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_nested_editor_roundtrip_and_owner_draft_only(tmp_path):
    from PySide6.QtWidgets import QApplication
    from veriagent.desktop import ConditionEditor, MainWindow
    from veriagent.owner import OwnerService
    from test_desktop_foundation import seed_home
    app = QApplication.instance() or QApplication([])
    condition = {"op": "not", "child": {"op": "any", "children": [
        {"op": "atom", "name": "approved"}, {"op": "all", "children": [
            {"op": "atom", "name": "external"}, {"op": "not", "child": {"op": "atom", "name": "confidential"}}]}]}}
    editor = ConditionEditor(condition)
    assert editor.condition() == condition
    seed_home(tmp_path)
    owner = OwnerService(tmp_path)
    before = (tmp_path / "policies/active.json").read_bytes()
    window = MainWindow(owner)
    assert window.tabs.count() == 2
    window.table.selectRow(0)
    window.toggle_rule()
    assert (tmp_path / "policies/active.json").read_bytes() == before
    assert not owner.begin().rules[0].enabled
    window.close()
    editor.close()
