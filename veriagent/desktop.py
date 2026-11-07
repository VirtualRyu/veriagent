"""Small native owner companion. Chat and enforcement stay in Codex/MCP."""
import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .authoring import bootstrap_demo, controlled_english
from .models import PolicyBundle, PolicyRule
from .owner import OwnerRule, OwnerService, now
from .policy import compile_bundle
from .rendering import artifacts, logic
from .state import resolve_home, select_home


def button(text, callback, layout):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def text_panel(text=""):
    panel = QPlainTextEdit()
    panel.setReadOnly(True)
    panel.setPlainText(text)
    return panel


def json_text(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


def dialog_buttons(dialog, layout, accept_label=None):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.rejected.connect(dialog.reject)
    if accept_label:
        accept = box.addButton(accept_label, QDialogButtonBox.ButtonRole.AcceptRole)
    else:
        accept = None
    layout.addWidget(box)
    return accept


class ConditionEditor(QWidget):
    def __init__(self, condition=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Operator / group", "Fact"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)
        controls = QHBoxLayout()
        button("Add child", self.add_child, controls)
        button("Remove selected child", self.remove_child, controls)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Nested groups are preserved. NOT has one child; AND/OR require at least one."))
        self.root = self.add_node(None, condition or {"op": "atom", "name": "confidential"})
        self.tree.expandAll()

    def add_node(self, parent, value):
        item = QTreeWidgetItem(parent or self.tree)
        op = QComboBox()
        for label, key in (("Fact", "atom"), ("NOT", "not"), ("AND", "all"), ("OR", "any")):
            op.addItem(label, key)
        op.setCurrentIndex(op.findData(value["op"]))
        fact = QComboBox()
        fact.addItems(["confidential", "external", "approved"])
        fact.setCurrentText(value.get("name", "confidential"))
        fact.setEnabled(value["op"] == "atom")
        self.tree.setItemWidget(item, 0, op)
        self.tree.setItemWidget(item, 1, fact)
        children = [value["child"]] if value["op"] == "not" else value.get("children", [])
        for child in children:
            self.add_node(item, child)
        op.currentIndexChanged.connect(lambda _index, node=item: self.change_operator(node))
        return item

    def node_value(self, item):
        op = self.tree.itemWidget(item, 0).currentData()
        if op == "atom":
            return {"op": op, "name": self.tree.itemWidget(item, 1).currentText()}
        children = [self.node_value(item.child(i)) for i in range(item.childCount())]
        if op == "not":
            if len(children) != 1:
                raise ValueError("NOT requires exactly one child")
            return {"op": op, "child": children[0]}
        return {"op": op, "children": children}

    def condition(self):
        return self.node_value(self.root)

    def change_operator(self, item):
        op = self.tree.itemWidget(item, 0).currentData()
        self.tree.itemWidget(item, 1).setEnabled(op == "atom")
        if op == "atom":
            item.takeChildren()
        elif item.childCount() == 0:
            self.add_node(item, {"op": "atom", "name": "confidential"})
        elif op == "not" and item.childCount() > 1:
            values = [self.node_value(item.child(i)) for i in range(item.childCount())]
            item.takeChildren()
            self.add_node(item, {"op": "all", "children": values})
        self.tree.expandAll()

    def add_child(self):
        item = self.tree.currentItem() or self.root
        op = self.tree.itemWidget(item, 0).currentData()
        if op == "atom" or (op == "not" and item.childCount()):
            QMessageBox.information(self, "Choose a group", "Select an AND/OR group to add another child.")
            return
        self.add_node(item, {"op": "atom", "name": "confidential"})
        item.setExpanded(True)

    def remove_child(self):
        item = self.tree.currentItem()
        if item and item.parent():
            item.parent().removeChild(item)


class RuleDialog(QDialog):
    def __init__(self, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit owner draft rule" if existing else "Add owner draft rule")
        self.resize(760, 650)
        self.existing, self.value = existing, None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_input = QLineEdit(existing.title if existing else "New rule")
        self.effect = QComboBox()
        self.effect.addItems(["BLOCK", "HUMAN_REVIEW", "ALLOW"])
        self.effect.setCurrentText(existing.rule.effect if existing else "BLOCK")
        self.enabled = QCheckBox("Enabled in the proposed bundle")
        self.enabled.setChecked(existing.enabled if existing else True)
        form.addRow("Title", self.title_input)
        form.addRow("Effect", self.effect)
        form.addRow("", self.enabled)
        layout.addLayout(form)
        self.editor = ConditionEditor(existing.rule.condition.model_dump(mode="json") if existing else None)
        layout.addWidget(self.editor)
        if existing and existing.original_source:
            source = text_panel("Preserved original wording (not regenerated):\n" + existing.original_source)
            source.setMaximumHeight(90)
            layout.addWidget(source)
        layout.addWidget(QLabel("Saving changes only the owner draft. Review and Apply are required for enforcement."))
        save = dialog_buttons(self, layout, "Save draft rule")
        save.clicked.connect(self.save)

    def save(self):
        try:
            condition = self.editor.condition()
            rule = PolicyRule(id=self.existing.rule.id if self.existing else "R-" + uuid4().hex[:10],
                              source_text="Visually authored rule", effect=self.effect.currentText(), condition=condition)
            rule = rule.model_copy(update={"source_text": f"{rule.effect} when {controlled_english(rule.condition)}"})
            self.value = OwnerRule(title=self.title_input.text().strip(), rule=rule, enabled=self.enabled.isChecked(),
                                   original_source=self.existing.original_source if self.existing else None,
                                   origin="visual edit" if self.existing else "visual creation", edited_at=now())
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid rule", str(exc))


class PreviewDialog(QDialog):
    def __init__(self, owner, draft, parent=None):
        super().__init__(parent)
        self.owner, self.draft = owner, draft
        self.setWindowTitle("Test request — no action executes")
        self.resize(840, 660)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.document = QComboBox()
        for doc in owner.store.list_documents():
            self.document.addItem(f"{doc.name} ({doc.id}; {doc.classification or 'unclassified'})", doc.id)
        self.recipient = QLineEdit("alice@example.org")
        form.addRow("Synthetic document", self.document)
        form.addRow("Recipient", self.recipient)
        layout.addLayout(form)
        button("Preview active and draft", self.preview, layout)
        self.result = text_panel("Choose a request. Preview writes no outbox, approval or live-action audit record.")
        layout.addWidget(self.result)
        details = QGroupBox("Logic / actual SAT details (read-only)")
        details.setCheckable(True)
        details.setChecked(False)
        inner = QVBoxLayout(details)
        self.sat = text_panel()
        self.sat.setVisible(False)
        inner.addWidget(self.sat)
        details.toggled.connect(self.sat.setVisible)
        layout.addWidget(details)
        dialog_buttons(self, layout)

    def preview(self):
        try:
            if self.document.currentData() is None:
                raise ValueError("No synthetic catalog is available. Initialize samples explicitly in Connection / State.")
            report = self.owner.preview(self.draft, self.document.currentData(), self.recipient.text())
            sections = []
            for label, data in report.items():
                sections.append(f"{label.upper()} — {data['policy_revision'] or 'unavailable'}\n"
                    f"Decision: {data['decision']} (preview only; no action executes)\n"
                    f"Facts: {json_text(data['facts']) if data['facts'] else 'Required metadata unavailable; solver not evaluated'}\n"
                    f"Matched rules: {', '.join(data['matched_policy_ids']) or 'none'}\n{data['reason']}")
            changed = report["active"]["decision"] != report["draft"]["decision"]
            self.result.setPlainText("\n\n".join(sections) + ("\n\nProposed changes alter this decision." if changed else "\n\nSame decision under active and draft bundles."))
            self.sat.setPlainText(json_text({k: v["trace"] for k, v in report.items()}))
        except (ValueError, OSError, Exception) as exc:
            self.result.setPlainText(f"Preview error — no enforcement result or execution:\n{exc}")
            self.sat.clear()


class ConnectionDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Codex connection and owner state")
        self.resize(800, 590)
        layout = QVBoxLayout(self)
        self.details = text_panel()
        layout.addWidget(self.details)
        button("Refresh observed status", self.refresh_status, layout)
        button("Review / register VeriAgent with Codex", self.register, layout)
        button("Select existing state directory…", self.select_state, layout)
        button("Initialize synthetic samples here (only if empty)", self.initialize, layout)
        layout.addWidget(QLabel("Registration may need one client reopen. Policy Apply refreshes on subsequent requests.\n"
                               "Closing this window does not stop independently running MCP processes."))
        dialog_buttons(self, layout)
        self.refresh_status()

    def refresh_status(self):
        from .connection import status
        self.details.setPlainText(json_text(status(self.window.owner.home)))

    def register(self):
        from .connection import registration_command, register
        try:
            command = registration_command(self.window.owner.home)
            prompt = "Register/update only the 'veriagent' MCP entry; unrelated settings are preserved.\n\n" + json_text(command)
            if QMessageBox.question(self, "Review connection configuration", prompt) != QMessageBox.StandardButton.Yes:
                return
            QMessageBox.information(self, "Connection configured", register(self.window.owner.home))
            self.refresh_status()
        except Exception as exc:
            QMessageBox.warning(self, "Connection attention required", str(exc))

    def select_state(self):
        from .connection import status
        suggested = status(self.window.owner.home).get("suggested_existing_state", str(self.window.owner.home))
        selected = QFileDialog.getExistingDirectory(self, "Select existing VeriAgent owner state", suggested)
        if not selected:
            return
        try:
            select_home(Path(selected), explicit_override=self.window.explicit_home)
            self.window.change_owner(OwnerService(Path(selected)))
            self.refresh_status()
            QMessageBox.information(self, "Owner state selected", "Existing policies/evidence retained. Review the Codex registration to use this same explicit directory.")
        except Exception as exc:
            QMessageBox.warning(self, "State selection failed", str(exc))

    def initialize(self):
        try:
            self.window.draft = self.window.owner.initialize_samples(self.window.draft)
            self.window.refresh_rules()
            self.refresh_status()
        except Exception as exc:
            QMessageBox.warning(self, "Samples were not initialized", str(exc))


class MainWindow(QMainWindow):
    def __init__(self, owner, explicit_home=None):
        super().__init__()
        self.owner, self.draft = owner, owner.begin()
        self.explicit_home = explicit_home
        self.setWindowTitle("VeriAgent — Owner Companion / Sandbox")
        self.resize(1120, 780)
        root = QWidget()
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        title = QLabel("VeriAgent  /  Sandbox")
        title.setStyleSheet("font-size: 21px; font-weight: 600; padding: 8px 0;")
        top.addWidget(title)
        top.addStretch()
        self.state_label = QLabel()
        top.addWidget(self.state_label)
        button("Connection / State", lambda: ConnectionDialog(self).exec(), top)
        layout.addLayout(top)
        label = QLabel("Simulated document sharing · Policies govern VeriAgent tools · HUMAN_REVIEW pauses without execution")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.build_rules()
        self.build_activity()
        self.setCentralWidget(root)
        self.refresh_rules()
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(lambda: self.refresh_activity() if self.tabs.currentIndex() == 1 else self.refresh_status())
        self.timer.start()
        self.tabs.currentChanged.connect(lambda i: self.refresh_activity() if i == 1 else None)

    def guard(self, callback):
        try:
            return callback()
        except Exception as exc:
            QMessageBox.warning(self, "Owner action could not complete", str(exc))
            return None

    def build_rules(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("View"))
        self.view = QComboBox()
        self.view.addItems(["Natural Language", "Logic"])
        bar.addWidget(self.view)
        bar.addStretch()
        button("Discard draft / Reload active", lambda: self.guard(self.reload_active), bar)
        layout.addLayout(bar)
        self.rule_basis = QLabel()
        layout.addWidget(self.rule_basis)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Rule", "Enabled", "Trigger", "Effect"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(3, 140)
        layout.addWidget(self.table, 3)
        controls = QHBoxLayout()
        button("Add", lambda: self.guard(lambda: self.edit_rule(new=True)), controls)
        button("Edit", lambda: self.guard(self.edit_rule), controls)
        button("Duplicate", lambda: self.guard(self.duplicate_rule), controls)
        button("Enable / Disable", lambda: self.guard(self.toggle_rule), controls)
        button("Delete", lambda: self.guard(self.delete_rule), controls)
        controls.addStretch()
        button("Test request", lambda: PreviewDialog(self.owner, self.draft, self).exec(), controls)
        button("History", lambda: self.guard(self.show_history), controls)
        layout.addLayout(controls)
        self.details_group = QGroupBox("Selected rule · source / provenance / actual CNF and DIMACS")
        self.details_group.setCheckable(True)
        self.details_group.setChecked(False)
        inner = QVBoxLayout(self.details_group)
        self.rule_details = text_panel()
        self.rule_details.setVisible(False)
        self.details_group.toggled.connect(self.rule_details.setVisible)
        inner.addWidget(self.rule_details)
        layout.addWidget(self.details_group)
        applybar = QHBoxLayout()
        self.draft_label = QLabel()
        applybar.addWidget(self.draft_label)
        applybar.addStretch()
        button("Review changes / Apply…", lambda: self.guard(self.review_apply), applybar)
        layout.addLayout(applybar)
        self.table.itemSelectionChanged.connect(self.selected_details)
        self.table.doubleClicked.connect(lambda: self.guard(self.edit_rule))
        self.view.currentTextChanged.connect(lambda _text: self.refresh_rules())
        self.tabs.addTab(page, "Rules")

    def build_activity(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Actual persisted requests · Refreshes while this tab is visible"))
        bar.addStretch()
        button("Refresh", self.refresh_activity, bar)
        layout.addLayout(bar)
        self.activity_table = QTableWidget(0, 5)
        self.activity_table.setHorizontalHeaderLabels(["Time (UTC)", "Document", "Recipient", "Decision", "Execution"])
        self.activity_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.activity_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.activity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.activity_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.activity_details = text_panel("Select a request to inspect its recorded revision, facts and actual solver query.")
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.activity_table)
        splitter.addWidget(self.activity_details)
        layout.addWidget(splitter)
        self.activity_table.itemSelectionChanged.connect(self.selected_activity)
        self.activity_rows = []
        self.tabs.addTab(page, "Activity")

    def change_owner(self, owner):
        self.owner, self.draft = owner, owner.begin()
        self.refresh_rules()
        self.refresh_activity()

    def refresh_status(self):
        revision = self.owner.active_revision()
        self.state_label.setText("Active: " + (revision or "unavailable — actions paused"))
        stale = self.draft.base_pointer != self.owner.pointer()
        dirty = self.draft.rules != self.owner.baseline().rules or bool(self.draft.restored_from)
        self.rule_basis.setText("Displayed owner draft is based on: " + (self.draft.base_revision or "no active revision"))
        self.draft_label.setText("Stale draft — active policy changed; inspect History and reload before Apply" if stale else
                                 "Draft changes saved · active policy unchanged" if dirty else "No draft changes")
        self.statusBar().showMessage("Owner state: " + str(self.owner.home))

    def refresh_rules(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.draft.rules))
        for i, item in enumerate(self.draft.rules):
            condition = controlled_english(item.rule.condition) if self.view.currentIndex() == 0 else logic(item.rule.condition)
            values = [item.title, "Yes" if item.enabled else "No", condition, item.rule.effect]
            for column, value in enumerate(values):
                self.table.setItem(i, column, QTableWidgetItem(value))
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        self.refresh_status()
        self.selected_details()

    def selected(self):
        row = self.table.currentRow()
        return row if 0 <= row < len(self.draft.rules) else None

    def selected_details(self):
        row = self.selected()
        if row is None:
            self.rule_details.setPlainText("Select a rule. SAT details are read-only.")
            return
        item = self.draft.rules[row]
        compiled = compile_bundle(PolicyBundle(revision="owner-draft", rules=[item.rule])).rules[0]
        self.rule_details.setPlainText(json_text({"representation": "owner draft (disabled rules excluded from evaluation)",
            "based_on_revision": self.draft.base_revision, "title": item.title, "enabled": item.enabled,
            "original_source": item.original_source, "origin": item.origin, "visually_edited_at": item.edited_at,
            **artifacts(compiled)}))

    def persist(self, rules):
        self.draft = self.owner.save(self.draft.model_copy(update={"rules": tuple(rules)}))
        self.refresh_rules()

    def edit_rule(self, new=False):
        row = self.selected()
        if not new and row is None:
            return
        dialog = RuleDialog(None if new else self.draft.rules[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rules = list(self.draft.rules)
            if new:
                rules.append(dialog.value)
            else:
                rules[row] = dialog.value
            self.persist(rules)

    def duplicate_rule(self):
        row = self.selected()
        if row is not None:
            original = self.draft.rules[row]
            copied = original.model_copy(update={"title": (original.title[:110] + " copy"), "origin": "visual duplicate", "edited_at": now(),
                "rule": original.rule.model_copy(update={"id": "R-" + uuid4().hex[:10]})})
            self.persist([*self.draft.rules, copied])

    def toggle_rule(self):
        row = self.selected()
        if row is not None:
            rules = list(self.draft.rules)
            rules[row] = rules[row].model_copy(update={"enabled": not rules[row].enabled, "origin": "visual edit", "edited_at": now()})
            self.persist(rules)

    def delete_rule(self):
        row = self.selected()
        if row is not None:
            self.persist([rule for i, rule in enumerate(self.draft.rules) if i != row])

    def reload_active(self):
        if QMessageBox.question(self, "Reload active policy", "Discard the current owner draft and reload the active policy? Raw imported evidence and revisions are retained.") == QMessageBox.StandardButton.Yes:
            self.draft = self.owner.reload_active(self.draft)
            self.refresh_rules()

    def review_apply(self):
        report = self.owner.review(self.draft)
        dialog = QDialog(self)
        dialog.setWindowTitle("Review complete policy bundle")
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Apply creates a new immutable revision. Next MCP actions read it automatically."))
        details = {"base_revision": report["base_revision"], "changes": report["changes"], "warnings": report["warnings"],
                   "rules": [{"title": item.title, "enabled": item.enabled, "effect": item.rule.effect,
                              "meaning": controlled_english(item.rule.condition), "logic": logic(item.rule.condition)} for item in self.draft.rules],
                   "all_eight_complete_assignments": report["truth_table"]}
        layout.addWidget(text_panel(json_text(details)))
        apply = dialog_buttons(dialog, layout, "Apply reviewed changes")
        apply.setEnabled(bool(report["changes"]) or bool(self.draft.restored_from) or not self.owner.active_revision())
        def activate():
            try:
                self.owner.apply(self.draft, report["token"])
            except Exception as exc:
                QMessageBox.warning(dialog, "Apply failed — active policy was not replaced", str(exc))
                return
            try:
                self.draft = self.owner.begin()
                self.refresh_rules()
                dialog.accept()
            except Exception as exc:
                QMessageBox.warning(dialog, "Policy applied — window refresh needs attention", str(exc))
        apply.clicked.connect(activate)
        dialog.exec()

    def show_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Immutable revision history")
        dialog.resize(900, 660)
        layout = QVBoxLayout(dialog)
        rows = self.owner.history()
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Revision", "Created (UTC)", "Summary"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, row in enumerate(rows):
            for j, value in enumerate([row["revision"] + (" (active)" if row["active"] else ""), row["time"], "; ".join(row["summary"]) ]):
                table.setItem(i, j, QTableWidgetItem(value))
        layout.addWidget(table)
        detail = text_panel("Inspecting history is read-only. Restore creates a draft for later review and Apply.")
        layout.addWidget(detail)
        def inspect():
            i = table.currentRow()
            if 0 <= i < len(rows):
                rules = self.owner.revision_rules(rows[i]["revision"])
                detail.setPlainText(json_text([r.model_dump(mode="json") for r in rules]))
        table.itemSelectionChanged.connect(inspect)
        restore = dialog_buttons(dialog, layout, "Use selected revision as new draft")
        def restore_selected():
            i = table.currentRow()
            if 0 <= i < len(rows):
                self.draft = self.owner.restore(self.draft, rows[i]["revision"])
                self.refresh_rules()
                dialog.accept()
        restore.clicked.connect(lambda: self.guard(restore_selected))
        dialog.exec()

    def refresh_activity(self):
        try:
            selected_id = self.activity_rows[self.activity_table.currentRow()]["request_id"] if self.activity_rows and self.activity_table.currentRow() >= 0 else None
            self.activity_rows = self.owner.store.activity()
            self.activity_table.blockSignals(True)
            self.activity_table.setRowCount(len(self.activity_rows))
            selected_row = 0
            for i, row in enumerate(self.activity_rows):
                if row["request_id"] == selected_id:
                    selected_row = i
                execution = "Executed (simulated)" if row["executed"] else "Paused — no action executed" if row["decision"] == "HUMAN_REVIEW" else "No action executed"
                for j, value in enumerate([row["created_at"], row["document_id"], row["recipient"], row["decision"], execution]):
                    self.activity_table.setItem(i, j, QTableWidgetItem(value))
            self.activity_table.blockSignals(False)
            if self.activity_rows:
                self.activity_table.selectRow(selected_row)
            self.selected_activity()
            self.refresh_status()
        except Exception as exc:
            self.activity_details.setPlainText(f"Activity unavailable: {exc}")

    def selected_activity(self):
        i = self.activity_table.currentRow()
        if 0 <= i < len(self.activity_rows):
            row = self.activity_rows[i]
            details = {k: row.get(k) for k in ("request_id", "created_at", "document_id", "recipient", "decision", "executed", "policy_revision", "matched_policy_ids", "reason", "outbox_id")}
            details["facts"] = row["facts"] if row["facts"] is not None else "Not recorded for this historical request"
            details["solver_trace"] = row["trace"] if row["trace"] is not None else "Detailed solver trace was not recorded; it is not reconstructed from today's policy/catalog"
            self.activity_details.setPlainText(json_text(details))


def configure_style(app):
    app.setApplicationName("VeriAgent")
    app.setStyle("Fusion")
    palette = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Button):
        palette.setColor(role, QColor("#252a32"))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase):
        palette.setColor(role, QColor("#1c2027"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(role, QColor("#e9edf3"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3b6583"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    configure_style(app)
    try:
        window = MainWindow(OwnerService(resolve_home(args.home)), explicit_home=args.home)
    except Exception as exc:
        QMessageBox.critical(None, "VeriAgent could not open owner state", str(exc))
        raise SystemExit(1)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
