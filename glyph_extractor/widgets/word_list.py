"""Left panel: editable list of OCR words."""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..models import Word


class WordListWidget(QWidget):
    """Displays the list of detected words with edit/delete support."""

    word_selected = pyqtSignal(object)  # emits a Word or None
    words_changed = pyqtSignal()        # emitted after edit or delete

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

    def _apply_edit_color(self, item: QListWidgetItem, word: Word) -> None:
        """Color the item green if the user has edited its text.

        Unedited items use the palette's default text color so they remain
        visible under both light and dark themes.
        """
        if word.text != word.original_text:
            item.setForeground(QColor(0, 170, 0))
        else:
            default_color = self.list_widget.palette().color(QPalette.Text)
            item.setForeground(default_color)

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
        self._apply_edit_color(item, word)
        self.words_changed.emit()

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

    def keyPressEvent(self, event):  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key_Delete:
            self.delete_selected()
        else:
            super().keyPressEvent(event)