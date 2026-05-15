"""VHDL export dock panel.

Provides:
  - "Generate" — runs StructuralVhdlGenerator on the current design.
  - "Check GHDL" — writes output to a temp file, runs ``ghdl -a``, and
    highlights error lines in the text view.
  - "Copy" / "Save…" — clipboard and file export.

The panel receives two callables at construction time:

  design_getter()  → Design         (from DesignScene.get_design)
  library_getter() → dict[str, ComponentDefinition]
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pipeline_designer.domain.models.component import ComponentDefinition
from pipeline_designer.domain.models.design import Design
from pipeline_designer.domain.vhdl import StructuralVhdlGenerator
from pipeline_designer.infrastructure.ghdl import GhdlRunner


class VhdlExportPanel(QWidget):
    """Dock panel for structural VHDL generation and GHDL syntax checking."""

    def __init__(
        self,
        design_getter: Callable[[], Design],
        library_getter: Callable[[], dict[str, ComponentDefinition]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._design_getter  = design_getter
        self._library_getter = library_getter
        self._ghdl           = GhdlRunner()
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Toolbar row
        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(6)

        self._btn_generate = QPushButton("Generate")
        self._btn_generate.setToolTip("Generate VHDL from current design")
        self._btn_generate.clicked.connect(self._on_generate)

        self._btn_ghdl = QPushButton("Check GHDL")
        self._btn_ghdl.setToolTip("Run ghdl -a on the generated VHDL")
        self._btn_ghdl.clicked.connect(self._on_check_ghdl)

        self._btn_copy = QPushButton("Copy")
        self._btn_copy.setToolTip("Copy VHDL text to clipboard")
        self._btn_copy.clicked.connect(self._on_copy)

        self._btn_save = QPushButton("Save…")
        self._btn_save.setToolTip("Save VHDL to a .vhd file")
        self._btn_save.clicked.connect(self._on_save)

        self._status_label = QLabel()
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        toolbar_row.addWidget(self._btn_generate)
        toolbar_row.addWidget(self._btn_ghdl)
        toolbar_row.addWidget(self._btn_copy)
        toolbar_row.addWidget(self._btn_save)
        toolbar_row.addWidget(self._status_label)
        root.addLayout(toolbar_row)

        # Splitter: text view on top, error list on bottom
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._text_view = QPlainTextEdit()
        self._text_view.setReadOnly(True)
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(9)
        self._text_view.setFont(font)
        self._text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text_view.setPlaceholderText(
            'Click "Generate" to produce VHDL from the current design.'
        )
        splitter.addWidget(self._text_view)

        self._error_list = QListWidget()
        self._error_list.setMaximumHeight(130)
        self._error_list.itemClicked.connect(self._on_error_clicked)
        splitter.addWidget(self._error_list)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_generate(self) -> None:
        design  = self._design_getter()
        library = self._library_getter()

        gen  = StructuralVhdlGenerator(design, library)
        vhdl = gen.generate()

        self._text_view.setPlainText(vhdl)
        self._clear_highlights()
        self._error_list.clear()

        warnings = gen.warnings
        if warnings:
            self._set_status(f"{len(warnings)} warning(s)", warning=True)
            for msg in warnings:
                self._add_error_item(0, f"⚠ {msg}", "warning")
        else:
            self._set_status("Generated OK")

    def _on_check_ghdl(self) -> None:
        vhdl = self._text_view.toPlainText().strip()
        if not vhdl:
            self._set_status("Nothing to check — generate first", warning=True)
            return

        self._error_list.clear()
        self._clear_highlights()
        self._set_status("Running GHDL…")

        errors = self._ghdl.check(vhdl)

        if not errors:
            self._set_status("GHDL: no errors")
            item = QListWidgetItem("✓  No errors")
            item.setForeground(QColor(60, 180, 60))
            self._error_list.addItem(item)
            return

        error_count   = sum(1 for e in errors if e.severity == "error")
        warning_count = sum(1 for e in errors if e.severity == "warning")
        self._set_status(
            f"GHDL: {error_count} error(s), {warning_count} warning(s)",
            warning=error_count > 0,
        )
        self._highlight_errors(errors)
        for err in errors:
            icon = "✗" if err.severity == "error" else "⚠"
            loc  = f"Line {err.line}" if err.line else "—"
            self._add_error_item(err.line, f"{icon}  {loc}: {err.message}", err.severity)

    def _on_copy(self) -> None:
        text = self._text_view.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._set_status("Copied to clipboard")

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save VHDL", "",
            "VHDL Files (*.vhd *.vhdl);;All Files (*)",
        )
        if path:
            Path(path).write_text(self._text_view.toPlainText(), encoding="utf-8")
            self._set_status(f"Saved to {Path(path).name}")

    # ── Error display helpers ─────────────────────────────────────────────────

    def _add_error_item(self, line: int, text: str, severity: str) -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, line)
        if severity == "error":
            item.setForeground(QColor(210, 50, 50))
        elif severity == "warning":
            item.setForeground(QColor(200, 140, 0))
        else:
            item.setForeground(QColor(100, 100, 100))
        self._error_list.addItem(item)

    def _on_error_clicked(self, item: QListWidgetItem) -> None:
        line: int = item.data(Qt.ItemDataRole.UserRole) or 0
        if line <= 0:
            return
        block = self._text_view.document().findBlockByLineNumber(line - 1)
        if block.isValid():
            cursor = QTextCursor(block)
            self._text_view.setTextCursor(cursor)
            self._text_view.ensureCursorVisible()

    def _highlight_errors(self, errors) -> None:
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 80, 80, 55))

        selections = []
        doc = self._text_view.document()
        seen_lines: set[int] = set()
        for err in errors:
            if err.line <= 0 or err.line in seen_lines:
                continue
            seen_lines.add(err.line)
            block = doc.findBlockByLineNumber(err.line - 1)
            if not block.isValid():
                continue
            from PySide6.QtWidgets import QTextEdit
            sel = QTextEdit.ExtraSelection()
            sel.format = fmt
            sel.cursor = QTextCursor(block)
            sel.cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            selections.append(sel)

        self._text_view.setExtraSelections(selections)

    def _clear_highlights(self) -> None:
        self._text_view.setExtraSelections([])

    def _set_status(self, msg: str, warning: bool = False) -> None:
        self._status_label.setText(msg)
        color = "#c05000" if warning else "#444444"
        self._status_label.setStyleSheet(f"color: {color}; font-style: italic;")
