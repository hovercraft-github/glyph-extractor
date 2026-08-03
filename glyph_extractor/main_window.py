"""Main window assembling the panels, project DB, and menu actions.

Layout:
- Left: word list (OCR review).
- Center-right (top): symbol view (image fragment + bboxes).
- Center-right (bottom): contour view (rendered contours).
- Far right: glyph browser (collected codepoints + instance previews).
- Bottom: status bar (project name + codepoint/instance counts).

Menus:
- File: Open PNG…, Export CSV… (fallback), Quit.
- Project: New, Open, Save, Save As, Build Font….
"""
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
    QStatusBar,
    QWidget,
)

from .contours import fill_contours_for_symbols
from .export import export_csv
from .font_builder import build_font
from .models import compute_line_baselines
from .ocr import extract_words
from .project import Project, create_project, load_project, save_project
from .widgets.contour_view import ContourViewWidget
from .widgets.glyph_browser import GlyphBrowserWidget
from .widgets.settings_dialog import SettingsDialog
from .widgets.symbol_view import SymbolViewWidget
from .widgets.word_list import WordListWidget


class MainWindow(QMainWindow):
    """Glyph extractor window with project DB + font building."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Glyph Extractor")
        self.resize(1400, 820)

        self._image: np.ndarray | None = None
        self._gray: np.ndarray | None = None
        self._image_path: str = ""
        self._project: Project | None = None

        # --- Widgets ---
        self.word_list = WordListWidget()
        self.symbol_view = SymbolViewWidget()
        self.contour_view = ContourViewWidget()
        self.glyph_browser = GlyphBrowserWidget()

        # Right side: vertical split of symbol view (top) and contour view (bottom).
        right_split = QSplitter(Qt.Vertical)
        right_split.addWidget(self.symbol_view)
        right_split.addWidget(self.contour_view)
        right_split.setSizes([400, 300])

        # Center: horizontal split of word list (left) and right panels.
        center_split = QSplitter(Qt.Horizontal)
        center_split.addWidget(self.word_list)
        center_split.addWidget(right_split)
        center_split.setSizes([250, 650])

        # Main: center + glyph browser (far right).
        main_split = QSplitter(Qt.Horizontal)
        main_split.addWidget(center_split)
        main_split.addWidget(self.glyph_browser)
        main_split.setSizes([900, 480])

        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(main_split)
        self.setCentralWidget(container)

        # --- Status bar ---
        self.setStatusBar(QStatusBar())
        self._update_status_bar()

        # --- Signals ---
        self.word_list.word_selected.connect(self._on_word_selected)
        self.word_list.words_changed.connect(self._on_words_changed)
        self.word_list.word_marked.connect(self._on_mark_changed)
        self.glyph_browser.project_changed.connect(self._on_project_changed)

        # Synchronize zoom between the two center-right panels.
        self.symbol_view.zoom_changed.connect(self._on_zoom_changed)
        self.contour_view.zoom_changed.connect(self._on_zoom_changed)

        # --- Menu ---
        self._build_menu()

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        # --- File menu ---
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

        # --- Project menu ---
        proj_menu = menubar.addMenu("Project")

        new_action = QAction("New Project...", self)
        new_action.triggered.connect(self.new_project)
        proj_menu.addAction(new_action)

        open_proj_action = QAction("Open Project...", self)
        open_proj_action.triggered.connect(self.open_project)
        proj_menu.addAction(open_proj_action)

        proj_menu.addSeparator()

        save_action = QAction("Save Project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project)
        proj_menu.addAction(save_action)

        save_as_action = QAction("Save Project As...", self)
        save_as_action.triggered.connect(self.save_project_as)
        proj_menu.addAction(save_as_action)

        proj_menu.addSeparator()

        build_action = QAction("Build Font...", self)
        build_action.triggered.connect(self.build_font)
        proj_menu.addAction(build_action)

        proj_menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.edit_settings)
        proj_menu.addAction(settings_action)

    # --- Project actions ---

    def new_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "New project", "", "Glyph projects (*.glyphproj);;All files (*)"
        )
        if not path:
            return
        if not path.endswith(".glyphproj"):
            path += ".glyphproj"
        try:
            self._project = create_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Could not create project:\n{exc}")
            return
        self.glyph_browser.set_project(self._project)
        self._update_status_bar()
        QMessageBox.information(self, "Project created", f"New project:\n{path}")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", "", "Glyph projects (*.glyphproj);;All files (*)"
        )
        if not path:
            return
        try:
            self._project = load_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Could not open project:\n{exc}")
            return
        self.glyph_browser.set_project(self._project)
        self._update_status_bar()

    def save_project(self) -> None:
        if self._project is None:
            QMessageBox.information(self, "No project", "Open or create a project first.")
            return
        if self._project.path is None:
            self.save_project_as()
            return
        try:
            save_project(self._project)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save error", f"Failed to save:\n{exc}")
            return
        self._update_status_bar()

    def save_project_as(self) -> None:
        if self._project is None:
            QMessageBox.information(self, "No project", "Open or create a project first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as", "", "Glyph projects (*.glyphproj);;All files (*)"
        )
        if not path:
            return
        if not path.endswith(".glyphproj"):
            path += ".glyphproj"
        try:
            save_project(self._project, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save error", f"Failed to save:\n{exc}")
            return
        self._update_status_bar()

    def build_font(self) -> None:
        if self._project is None:
            QMessageBox.information(self, "No project", "Open or create a project first.")
            return
        if not self._project.glyphs:
            QMessageBox.information(
                self, "No glyphs", "Collect some glyphs (mark words) before building a font."
            )
            return
        default_name = (self._project.name or "handwriting") + ".ttf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Build font", default_name, "TrueType fonts (*.ttf);;All files (*)"
        )
        if not path:
            return
        if not path.endswith(".ttf"):
            path += ".ttf"
        try:
            build_font(self._project, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Build error", f"Failed to build font:\n{exc}")
            return
        QMessageBox.information(
            self,
            "Font built",
            f"TrueType font written to:\n{path}\n\n"
            f"Glyphs: {len(self._project.glyphs)}",
        )

    def edit_settings(self) -> None:
        if self._project is None:
            QMessageBox.information(self, "No project", "Open or create a project first.")
            return
        dlg = SettingsDialog(self._project, self)
        if dlg.exec_() == SettingsDialog.Accepted:
            # Persist immediately if the project has a path.
            if self._project.path is not None:
                try:
                    save_project(self._project)
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(self, "Save error", f"Failed to save settings:\n{exc}")
            self._update_status_bar()

    # --- File actions ---

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
        self._image_path = path
        self.symbol_view.set_image(image)

        try:
            lang = self._project.ocr_lang if self._project is not None else "rus+eng"
            tcmd = self._project.tesseract_cmd if self._project is not None else ""
            words = extract_words(image, lang=lang, tesseract_cmd=tcmd)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "OCR error", f"Tesseract failed:\n{exc}")
            return

        # Pre-compute contours for every symbol.
        for word in words:
            fill_contours_for_symbols(self._gray, word.symbols)

        # Estimate baselines per text line so floating punctuation (e.g. ``"``)
        # inherits the baseline from neighbouring letters on the same line
        # instead of collapsing onto its own glyph bottom.
        compute_line_baselines(words)

        self.word_list.set_words(words)
        self.setWindowTitle(f"Glyph Extractor - {os.path.basename(path)}")

    def export(self) -> None:
        words = self.word_list.get_words()
        if not words:
            QMessageBox.information(self, "Nothing to export", "No words to export.")
            return
        marked = [w for w in words if w.marked]
        if not marked:
            QMessageBox.information(
                self,
                "Nothing to export",
                "No marked (green) words to export.\n"
                "Press Enter on a word to mark it for export.",
            )
            return
        if self._image is None:
            QMessageBox.warning(self, "No image", "Open an image first.")
            return
        # Compute baseline for each marked word.
        for w in marked:
            w.compute_baseline()
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

    def _on_zoom_changed(self, zoom: float) -> None:
        """Apply the same zoom to both center-right panels."""
        self.symbol_view.set_zoom(zoom)
        self.contour_view.set_zoom(zoom)

    def _on_words_changed(self) -> None:
        # Re-show current word to refresh panels after an edit.
        row = self.word_list.list_widget.currentRow()
        words = self.word_list.get_words()
        if 0 <= row < len(words):
            self._on_word_selected(words[row])

    def _on_mark_changed(self, word) -> None:
        """Called when a word's marked state changes.

        If a project is open and the word is now marked AND not yet committed,
        commit its glyphs to the project DB. Re-editing an already-committed
        word does not re-add duplicates. Unmarking does NOT remove
        already-collected glyphs (removal is done from the glyph browser) but
        does clear the green visual state.
        """
        if self._project is None or not word.marked:
            return
        if word.committed:
            return
        if self._image is None:
            return
        # Baselines are estimated per text line right after OCR
        # (compute_line_baselines), so a floating glyph like ``"`` inherits
        # the line baseline. Only fall back to a per-word estimate if no
        # baseline was set (e.g. words added outside the OCR path).
        if not word.baseline_y:
            word.compute_baseline()
        source = os.path.basename(self._image_path) if self._image_path else "image"
        try:
            added = self._project.add_word_glyphs(word, source_image=source, preview_image=self._image)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Project error", f"Failed to add glyphs:\n{exc}")
            return
        word.committed = True
        self.glyph_browser.refresh()
        self._update_status_bar()
        if added:
            self.statusBar().showMessage(
                f"Added {added} glyph(s) from word '{word.text}'", 3000
            )

    def _on_project_changed(self) -> None:
        """Glyph browser changed the project (set-best / delete). Save + refresh."""
        if self._project is not None and self._project.path is not None:
            try:
                save_project(self._project)
            except Exception:  # noqa: BLE001
                pass
        self._update_status_bar()

    # --- Status bar ---

    def _update_status_bar(self) -> None:
        if self._project is None:
            self.statusBar().showMessage("No project open")
            return
        summary = self._project.collection_summary()
        name = self._project.name or "Untitled"
        path = self._project.path or "(unsaved)"
        self.statusBar().showMessage(
            f"Project: {name}  |  {summary['codepoints']} codepoints  |  "
            f"{summary['instances']} instances  |  {path}"
        )