"""Main window assembling the three panels and menu actions."""
from __future__ import annotations

import os

import cv2
import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)

from .contours import fill_contours_for_symbols
from .export import export_csv
from .ocr import extract_words
from .widgets.contour_view import ContourViewWidget
from .widgets.symbol_view import SymbolViewWidget
from .widgets.word_list import WordListWidget


class MainWindow(QMainWindow):
    """3-panel glyph extractor window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glyph Extractor")
        self.resize(1100, 700)

        self._image: np.ndarray | None = None
        self._gray: np.ndarray | None = None

        # --- Widgets ---
        self.word_list = WordListWidget()
        self.symbol_view = SymbolViewWidget()
        self.contour_view = ContourViewWidget()

        # Right side: vertical split of symbol view (top) and contour view (bottom).
        right_split = QSplitter(Qt.Vertical)
        right_split.addWidget(self.symbol_view)
        right_split.addWidget(self.contour_view)
        right_split.setSizes([400, 300])

        # Main: horizontal split of word list (left) and right panels.
        main_split = QSplitter(Qt.Horizontal)
        main_split.addWidget(self.word_list)
        main_split.addWidget(right_split)
        main_split.setSizes([250, 850])

        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(main_split)
        self.setCentralWidget(container)

        # --- Signals ---
        self.word_list.word_selected.connect(self._on_word_selected)
        self.word_list.words_changed.connect(self._on_words_changed)

        # --- Menu ---
        self._build_menu()

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        open_action = QAction("Open PNG...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_image)
        file_menu.addAction(open_action)

        export_action = QAction("Export CSV...", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export)
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    # --- Actions ---
    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PNG image", "", "PNG images (*.png);;All files (*)"
        )
        if not path:
            return
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None:
            QMessageBox.warning(self, "Error", f"Could not load image:\n{path}")
            return
        self._image = image
        self._gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        self.symbol_view.set_image(image)

        try:
            words = extract_words(image)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "OCR error", f"Tesseract failed:\n{exc}")
            return

        # Pre-compute contours for every symbol.
        for word in words:
            fill_contours_for_symbols(self._gray, word.symbols)

        self.word_list.set_words(words)
        self.setWindowTitle(f"Glyph Extractor - {os.path.basename(path)}")

    def export(self) -> None:
        words = self.word_list.get_words()
        if not words:
            QMessageBox.information(self, "Nothing to export", "No words to export.")
            return
        if self._image is None:
            QMessageBox.warning(self, "No image", "Open an image first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output directory")
        if not out_dir:
            return
        try:
            csv_path = export_csv(words, self._image, out_dir)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export error", f"Failed to export:\n{exc}")
            return
        QMessageBox.information(
            self, "Export complete", f"CSV written to:\n{csv_path}"
        )

    # --- Slots ---
    def _on_word_selected(self, word) -> None:
        self.symbol_view.show_word(word)
        self.contour_view.show_word(word)

    def _on_words_changed(self) -> None:
        # Re-show current word to refresh panels after an edit.
        row = self.word_list.list_widget.currentRow()
        words = self.word_list.get_words()
        if 0 <= row < len(words):
            self._on_word_selected(words[row])