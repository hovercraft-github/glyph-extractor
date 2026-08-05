"""Project settings dialog.

A modal ``QDialog`` for editing project-specific settings that affect OCR
and font building:

- **OCR language(s)**: a ``+``-separated list of tesseract language codes
  (e.g. ``rus+eng``). Used by ``extract_words``.
- **Tesseract executable**: optional override path for the tesseract binary.
- **Font metrics**: ``units_per_em``, ``ascent``, ``descent`` (em units).

The dialog reads from / writes to a ``Project`` instance in place. The caller
is responsible for saving the project afterwards (the main window does this
on ``QDialog.Accepted``).
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..project import Project


# Common tesseract language codes offered as quick-pick checkboxes. The
# user can still type arbitrary codes in the line edit.
_COMMON_LANGS = [
    ("rus", "Russian (rus)"),
    ("eng", "English (eng)"),
    ("ukr", "Ukrainian (ukr)"),
    ("bel", "Belarusian (bel)"),
    ("kaz", "Kazakh (kaz)"),
    ("deu", "German (deu)"),
    ("fra", "French (fra)"),
    ("spa", "Spanish (spa)"),
]


class SettingsDialog(QDialog):
    """Modal dialog for editing a ``Project``'s settings."""

    def __init__(self, project: Project, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Project Settings")
        self._project = project

        root = QVBoxLayout(self)

        # --- OCR section ---
        ocr_title = QLabel("OCR")
        ocr_title.setStyleSheet("font-weight: bold;")
        root.addWidget(ocr_title)

        # Language quick-pick checkboxes + free-text field.
        lang_row = QHBoxLayout()
        self._lang_edit = QLineEdit(project.ocr_lang)
        self._lang_edit.setPlaceholderText("e.g. rus+eng")
        lang_row.addWidget(QLabel("Recognition languages:"))
        lang_row.addWidget(self._lang_edit, 1)
        root.addLayout(lang_row)

        self._lang_checks: dict[str, QCheckBox] = {}
        checks_row = QHBoxLayout()
        for code, label in _COMMON_LANGS:
            cb = QCheckBox(label)
            cb.toggled.connect(lambda checked, c=code: self._on_lang_toggled(c, checked))
            self._lang_checks[code] = cb
            checks_row.addWidget(cb)
        root.addLayout(checks_row)
        self._sync_lang_checks()

        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("Tesseract executable:"))
        self._cmd_edit = QLineEdit(project.tesseract_cmd)
        self._cmd_edit.setPlaceholderText("(leave empty for default)")
        cmd_row.addWidget(self._cmd_edit, 1)
        root.addLayout(cmd_row)

        root.addSpacing(8)

        # --- Letter classification section ---
        cls_title = QLabel("Letter classification")
        cls_title.setStyleSheet("font-weight: bold;")
        root.addWidget(cls_title)

        cls_form = QFormLayout()
        self._asc_edit = QLineEdit(project.ascenders)
        self._asc_edit.setPlaceholderText("e.g. бвдёйЙЁфbdfhklt")
        cls_form.addRow("Ascenders:", self._asc_edit)

        self._desc_edit = QLineEdit(project.descenders)
        self._desc_edit.setPlaceholderText("e.g. урцщфgjpqy")
        cls_form.addRow("Descenders:", self._desc_edit)
        root.addLayout(cls_form)

        cls_hint = QLabel(
            "Lowercase letters whose top rises above the x-height are "
            "ascenders; those whose bottom drops below the baseline are "
            "descenders. An uppercase letter in the ascenders list (e.g. "
            "Ё, Й) is a capital+ascender — its top normalizes above the "
            "capitals line. A letter in both lists (e.g. ф) is both. All "
            "other lowercase letters are regular. Not locale-dependent."
        )
        cls_hint.setWordWrap(True)
        cls_hint.setStyleSheet("color: gray; font-size: small;")
        root.addWidget(cls_hint)

        root.addSpacing(8)

        # --- Font metrics section ---
        font_title = QLabel("Font metrics (em units)")
        font_title.setStyleSheet("font-weight: bold;")
        root.addWidget(font_title)

        form = QFormLayout()
        self._upem_spin = QSpinBox()
        self._upem_spin.setRange(16, 16384)
        self._upem_spin.setValue(project.units_per_em)
        form.addRow("Units per em:", self._upem_spin)

        self._ascent_spin = QSpinBox()
        self._ascent_spin.setRange(-8192, 8192)
        self._ascent_spin.setValue(project.ascent)
        form.addRow("Ascent:", self._ascent_spin)

        self._descent_spin = QSpinBox()
        self._descent_spin.setRange(-8192, 8192)
        self._descent_spin.setValue(project.descent)
        form.addRow("Descent:", self._descent_spin)
        root.addLayout(form)

        root.addSpacing(12)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        if apply_btn is not None:
            apply_btn.clicked.connect(self._apply)
        root.addWidget(buttons)

    # --- Language checkbox sync ---

    def _current_lang_codes(self) -> set[str]:
        text = self._lang_edit.text().strip()
        return {c for c in text.split("+") if c}

    def _sync_lang_checks(self) -> None:
        codes = self._current_lang_codes()
        for code, cb in self._lang_checks.items():
            cb.blockSignals(True)
            cb.setChecked(code in codes)
            cb.blockSignals(False)

    def _on_lang_toggled(self, code: str, checked: bool) -> None:
        codes = self._current_lang_codes()
        if checked:
            codes.add(code)
        else:
            codes.discard(code)
        # Preserve a stable, sorted order for readability.
        self._lang_edit.setText("+".join(sorted(codes)))

    # --- Apply / accept ---

    def _apply(self) -> None:
        """Write the dialog's values into the project.

        On validation failure, shows a warning and leaves the project
        unchanged. Sets ``self._ok`` to True on success so ``accept`` can
        decide whether to close.
        """
        self._ok = False
        lang = self._lang_edit.text().strip()
        # Basic validation: non-empty codes, alphanumeric.
        for code in lang.split("+"):
            if code and not code.isalnum():
                QMessageBox.warning(
                    self, "Invalid language", f"Language code is not alphanumeric: {code!r}"
                )
                return
        upem = self._upem_spin.value()
        ascent = self._ascent_spin.value()
        descent = self._descent_spin.value()
        if ascent <= 0:
            QMessageBox.warning(self, "Invalid metrics", "Ascent must be positive.")
            return
        if descent > 0:
            QMessageBox.warning(self, "Invalid metrics", "Descent should be <= 0.")
            return
        if upem <= 0:
            QMessageBox.warning(self, "Invalid metrics", "Units per em must be positive.")
            return

        self._project.ocr_lang = lang
        self._project.tesseract_cmd = self._cmd_edit.text().strip()
        self._project.units_per_em = upem
        self._project.ascent = ascent
        self._project.descent = descent
        # Letter classification lists: strip whitespace (spaces would be
        # classified as floating anyway) and keep unique chars.
        asc = "".join(c for c in self._asc_edit.text() if not c.isspace())
        desc = "".join(c for c in self._desc_edit.text() if not c.isspace())
        self._project.ascenders = asc
        self._project.descenders = desc
        self._ok = True

    def accept(self) -> None:
        self._apply()
        if getattr(self, "_ok", False):
            super().accept()