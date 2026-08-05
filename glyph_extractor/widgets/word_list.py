"""Left panel: editable list of OCR words."""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..letter_kinds import word_is_committable
from ..models import Word
from ..project import DEFAULT_ASCENDERS, DEFAULT_DESCENDERS


class WordListWidget(QWidget):
    """Displays the list of detected words with edit/delete support."""

    word_selected = pyqtSignal(object)  # emits a Word or None
    words_changed = pyqtSignal()        # emitted after edit or delete
    word_marked = pyqtSignal(object)    # emitted after a word's mark state toggles

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        self.list_widget = QListWidget()
        # Increase font size by 20%.
        font: QFont = self.list_widget.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        self.list_widget.setFont(font)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setEditTriggers(
            QListWidget.DoubleClicked | QListWidget.EditKeyPressed
        )
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.list_widget)

        self._words: List[Word] = []
        self._suppress_changed = False
        # Per-project letter-classification sets for the committability
        # check. Defaults match ``letter_kinds``; the main window updates
        # these when a project is opened/created.
        self._ascenders: set = set(DEFAULT_ASCENDERS)
        self._descenders: set = set(DEFAULT_DESCENDERS)

    def set_classification(
        self, ascenders: str, descenders: str
    ) -> None:
        """Set the project's ascender/descender letter lists.

        Used by the committability check (faded / non-markable words) so it
        matches the project's per-project classification. Call before
        ``set_words`` so the initial coloring reflects the project.
        """
        self._ascenders = set(ascenders)
        self._descenders = set(descenders)

    def _is_committable(self, text: str) -> bool:
        return word_is_committable(text, self._ascenders, self._descenders)

    def set_words(self, words: List[Word]) -> None:
        self._words = words
        self._suppress_changed = True
        self.list_widget.clear()
        for word in words:
            item = QListWidgetItem(word.text)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self._apply_edit_color(item, word)
            self.list_widget.addItem(item)
        self._suppress_changed = False

    def get_words(self) -> List[Word]:
        return self._words

    def _on_selection_changed(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._words):
            self.word_selected.emit(self._words[row])
        else:
            self.word_selected.emit(None)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._suppress_changed:
            return
        row = self.list_widget.row(item)
        if row < 0 or row >= len(self._words):
            return
        word = self._words[row]
        new_text = item.text()
        if len(new_text) != len(word.text):
            # Reject: restore original text.
            QMessageBox.warning(
                self,
                "Invalid edit",
                f"Word length must stay the same (was {len(word.text)} chars, "
                f"got {len(new_text)}). Edit rejected.",
            )
            self._suppress_changed = True
            item.setText(word.text)
            self._suppress_changed = False
            return
        word.set_text(new_text)
        # Re-baseline the "original" text to the new value so unmarking
        # clears green.
        word.original_text = new_text
        # An edit just finished: mark the word green immediately so its
        # glyphs are collected without requiring a second Enter — but only
        # if the word is now committable (has a non-descender letter).
        # Disallowed words stay faded; the user can still edit them, and
        # once a non-descender letter is introduced they become committable.
        if self._is_committable(new_text):
            word.marked = True
        else:
            word.marked = False
        # Suppress itemChanged: setForeground triggers it, causing re-entrancy.
        self._suppress_changed = True
        self._apply_edit_color(item, word)
        self._suppress_changed = False
        self.words_changed.emit()
        if word.marked:
            self.word_marked.emit(word)

    def _apply_edit_color(self, item: QListWidgetItem, word: Word) -> None:
        """Color the item according to its mark state and committability.

        - **Marked & committable** → green (collected into the project).
        - **Disallowed** (no non-descender letter) → faded grey, regardless
          of the ``marked`` flag: such words cannot anchor a baseline, so
          they must not be committed. The user can still edit them; once a
          non-descender letter is introduced the word becomes committable
          and can be marked green.
        - **Unmarked & committable** → palette default text color (visible
          under both light and dark themes).
        """
        committable = self._is_committable(word.text)
        if not committable:
            # Disallowed: faded grey, never green.
            item.setForeground(QColor(150, 150, 150))
            return
        if word.marked:
            item.setForeground(QColor(0, 170, 0))
        else:
            default_color = self.list_widget.palette().color(QPalette.Text)
            item.setForeground(default_color)

    def _toggle_mark(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._words):
            return
        word = self._words[row]
        # Disallowed words (no non-descender letter) cannot be marked green:
        # they cannot anchor a baseline, so committing them would produce
        # wrong glyph positions. The user can still edit them; once a
        # non-descender letter is introduced the word becomes committable.
        if not word.marked and not self._is_committable(word.text):
            QMessageBox.information(
                self,
                "Cannot mark word",
                "This word contains no non-descender letter, so the "
                "baseline cannot be detected reliably.\n"
                "Edit the word to include at least one regular, capital, "
                "or ascender letter (e.g. a, A, b) to allow marking it.",
            )
            return
        word.marked = not word.marked
        item = self.list_widget.item(row)
        # Suppress itemChanged: setForeground triggers it, which would re-enter
        # _on_item_changed and re-mark the word green, making unmarking impossible.
        self._suppress_changed = True
        self._apply_edit_color(item, word)
        self._suppress_changed = False
        self.word_marked.emit(word)

    def delete_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._words):
            return
        self._suppress_changed = True
        self.list_widget.takeItem(row)
        del self._words[row]
        self._suppress_changed = False
        self.words_changed.emit()
        # select neighbor if available
        if self._words:
            new_row = min(row, len(self._words) - 1)
            self.list_widget.setCurrentRow(new_row)
        else:
            self.word_selected.emit(None)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt naming)
        key = event.key()
        if key == Qt.Key_Delete:
            self.delete_selected()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            # Enter toggles the "green" marked state (only when not editing).
            if not self.list_widget.state() == QAbstractItemView.EditingState:
                self._toggle_mark()
                return
        # Printable character: enter edit mode immediately.
        text = event.text()
        if text and text.isprintable() and not event.modifiers():
            row = self.list_widget.currentRow()
            if 0 <= row < len(self._words):
                self.list_widget.edit(self.list_widget.item(row))
        super().keyPressEvent(event)