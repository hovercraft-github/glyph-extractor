"""Glyph browser widget: review collected codepoints and pick the best instance.

Layout:
- Left: a list of collected codepoints, each row showing the character, its
  hex codepoint, and an instance-count badge. The currently selected ("best")
  instance is implied by the project's ``selected`` map.
- Right (top): for the selected codepoint, a vertical list of its instances;
  each row shows the raster preview (cropped glyph PNG), the vectorized
  outline preview, and a metrics summary (bbox, advance width, OCR conf,
  baseline offset, parts/holes). The chosen instance is highlighted.
- Right (bottom): buttons — "Set as best", "Delete instance", "Re-vectorize".

Signals:
- ``project_changed`` — emitted when the user sets a new best instance or
  deletes one. The main window uses this to save the project and refresh the
  status bar.
"""
from __future__ import annotations

import base64
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..letter_kinds import word_kinds_labels
from ..project import GlyphInstance, Project
from ..vectorize import advance_width_em, normalize_to_upem, vectorize_instance
from .vector_preview import VectorPreviewWidget


class GlyphBrowserWidget(QWidget):
    """Browse collected glyphs and choose the best instance per codepoint."""

    project_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._current_cp: Optional[str] = None

        # --- Left: codepoint list ---
        self.cp_list = QListWidget()
        self.cp_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cp_list.currentItemChanged.connect(self._on_cp_changed)
        cp_label = QLabel("Collected codepoints")
        cp_label.setStyleSheet("font-weight: bold;")

        left = QVBoxLayout()
        left.addWidget(cp_label)
        left.addWidget(self.cp_list)

        left_frame = QFrame()
        left_frame.setLayout(left)

        # --- Right: instance list + preview + controls ---
        self.instance_list = QListWidget()
        self.instance_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.instance_list.currentItemChanged.connect(self._on_instance_changed)
        self.instance_list.setIconSize(_icon_size())

        self.vector_preview = VectorPreviewWidget()
        self.vector_preview.setMinimumHeight(180)

        self.metrics_view = QTextEdit()
        self.metrics_view.setReadOnly(True)
        self.metrics_view.setMaximumHeight(120)

        self.btn_set_best = QPushButton("Set as best")
        self.btn_set_best.clicked.connect(self._set_as_best)
        self.btn_delete = QPushButton("Delete instance")
        self.btn_delete.clicked.connect(self._delete_instance)
        self.btn_revector = QPushButton("Re-vectorize")
        self.btn_revector.clicked.connect(self._revectorize)
        self.btn_put_baseline = QPushButton("Put on baseline")
        self.btn_put_baseline.clicked.connect(self._put_on_baseline)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_set_best)
        btn_row.addWidget(self.btn_delete)
        btn_row.addWidget(self.btn_revector)
        btn_row.addWidget(self.btn_put_baseline)
        btn_row.addStretch()

        right = QVBoxLayout()
        right.addWidget(QLabel("Instances"))
        right.addWidget(self.instance_list, 1)
        right.addWidget(QLabel("Vectorized preview"))
        right.addWidget(self.vector_preview, 2)
        right.addWidget(self.metrics_view)
        right.addLayout(btn_row)

        right_frame = QFrame()
        right_frame.setLayout(right)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setSizes([220, 580])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.addWidget(splitter)

        self._refresh_enabled()

    # --- Public API ---

    def set_project(self, project: Optional[Project]) -> None:
        self._project = project
        self._current_cp = None
        self.cp_list.clear()
        self.instance_list.clear()
        self.vector_preview.clear()
        self.metrics_view.clear()
        if project is not None:
            self._populate_cp_list()
        self._refresh_enabled()

    def refresh(self) -> None:
        """Rebuild the codepoint list, preserving the current selection."""
        if self._project is None:
            return
        current = self._current_cp
        self._populate_cp_list()
        if current and current in self._project.glyphs:
            items = self.cp_list.findItems(_cp_label(current, self._project), Qt.MatchExactly)
            if items:
                self.cp_list.setCurrentItem(items[0])
        self._refresh_enabled()

    # --- Internal: codepoint list ---

    def _populate_cp_list(self) -> None:
        self.cp_list.blockSignals(True)
        self.cp_list.clear()
        if self._project is None:
            self.cp_list.blockSignals(False)
            return
        for cp in self._project.codepoints():
            item = QListWidgetItem(_cp_label(cp, self._project))
            item.setData(Qt.UserRole, cp)
            self.cp_list.addItem(item)
        self.cp_list.blockSignals(False)

    def _on_cp_changed(self, current, _previous) -> None:
        if current is None or self._project is None:
            self._current_cp = None
            self.instance_list.clear()
            self.vector_preview.clear()
            self.metrics_view.clear()
            self._refresh_enabled()
            return
        cp = current.data(Qt.UserRole)
        self._current_cp = cp
        self._populate_instance_list(cp)

    def _populate_instance_list(self, cp: str) -> None:
        self.instance_list.blockSignals(True)
        self.instance_list.clear()
        instances = self._project.glyphs.get(cp, []) if self._project else []
        selected_idx = self._project.selected.get(cp, 0) if self._project else 0
        for i, inst in enumerate(instances):
            label = f"#{i}  {inst.char}  conf={inst.ocr_conf:.0f}"
            if i == selected_idx:
                label += "  ★"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            icon = _pixmap_from_instance(inst)
            if icon is not None:
                item.setIcon(icon)
            self.instance_list.addItem(item)
        self.instance_list.blockSignals(False)
        # Select the current best instance by default.
        if 0 <= selected_idx < self.instance_list.count():
            self.instance_list.setCurrentRow(selected_idx)
        else:
            self._show_instance(None)
        self._refresh_enabled()

    def _on_instance_changed(self, current, _previous) -> None:
        if current is None or self._project is None or self._current_cp is None:
            self._show_instance(None)
            return
        idx = current.data(Qt.UserRole)
        instances = self._project.glyphs.get(self._current_cp, [])
        if 0 <= idx < len(instances):
            self._show_instance(instances[idx])
        self._refresh_enabled()

    def _show_instance(self, inst: Optional[GlyphInstance]) -> None:
        if inst is None or self._project is None:
            self.vector_preview.clear()
            self.metrics_view.clear()
            return
        try:
            vg = normalize_to_upem(inst, self._project)
            adv = advance_width_em(inst, self._project)
        except Exception as exc:  # noqa: BLE001
            self.vector_preview.clear()
            self.metrics_view.setPlainText(f"Vectorization failed:\n{exc}")
            return
        self.vector_preview.set_glyph(
            vg,
            upem=self._project.units_per_em,
            ascent=self._project.ascent,
            descent=self._project.descent,
            advance=adv,
        )
        self.metrics_view.setPlainText(_metrics_text(inst, self._project))

    # --- Internal: controls ---

    def _current_instance(self) -> Optional[tuple[str, int, GlyphInstance]]:
        if self._project is None or self._current_cp is None:
            return None
        row = self.instance_list.currentRow()
        item = self.instance_list.currentItem()
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        instances = self._project.glyphs.get(self._current_cp, [])
        if not (0 <= idx < len(instances)):
            return None
        return (self._current_cp, idx, instances[idx])

    def _set_as_best(self) -> None:
        cur = self._current_instance()
        if cur is None or self._project is None:
            return
        cp, idx, _inst = cur
        self._project.set_selected(cp, idx)
        self._populate_instance_list(cp)
        self.project_changed.emit()

    def _delete_instance(self) -> None:
        cur = self._current_instance()
        if cur is None or self._project is None:
            return
        cp, idx, _inst = cur
        self._project.remove_instance(cp, idx)
        self._populate_instance_list(cp)
        self.refresh()
        self.project_changed.emit()

    def _revectorize(self) -> None:
        cur = self._current_instance()
        if cur is None:
            return
        _cp, _idx, inst = cur
        try:
            # Restore the original baseline (undo any "Put on baseline"
            # override) and re-trace the contours.
            inst.baseline_y = inst.original_baseline_y
            vectorize_instance(inst, force=True)
        except Exception as exc:  # noqa: BLE001
            self.metrics_view.setPlainText(f"Re-vectorization failed:\n{exc}")
            return
        self._show_instance(inst)
        self.project_changed.emit()

    def _put_on_baseline(self) -> None:
        """Force the glyph's baseline to its bbox bottom.

        This places the glyph exactly on the baseline. Useful for glyphs
        whose stored baseline is wrong, but **incorrect for descender
        glyphs** (p, у, д, ц, щ, …) whose bottom extends below the baseline.
        For those, re-OCR the source image to get a proper line-aware
        baseline.

        The original baseline is preserved in ``original_baseline_y`` so
        "Re-vectorize" can restore it.
        """
        cur = self._current_instance()
        if cur is None:
            return
        _cp, _idx, inst = cur
        inst.put_on_baseline()
        inst.vector_cache = None  # force re-vectorize with new baseline
        self._show_instance(inst)
        self.project_changed.emit()

    def _refresh_enabled(self) -> None:
        has = self._current_instance() is not None
        self.btn_set_best.setEnabled(has)
        self.btn_delete.setEnabled(has)
        self.btn_revector.setEnabled(has)
        self.btn_put_baseline.setEnabled(has)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cp_label(cp: str, project: Project) -> str:
    instances = project.glyphs.get(cp, [])
    char = instances[0].char if instances else "?"
    selected = project.selected.get(cp, 0)
    star = " ★" if 0 <= selected < len(instances) else ""
    return f"{char}  U+{cp.upper()}  ({len(instances)}){star}"


def _icon_size():
    from PyQt5.QtCore import QSize

    return QSize(48, 48)


def _pixmap_from_instance(inst: GlyphInstance) -> Optional[QIcon]:
    if not inst.preview_png_b64:
        return None
    try:
        data = base64.b64decode(inst.preview_png_b64)
        img = QImage.fromData(data, "PNG")
        if img.isNull():
            return None
        return QIcon(QPixmap.fromImage(img))
    except Exception:  # noqa: BLE001
        return None


def _metrics_text(inst: GlyphInstance, proj: Project) -> str:
    x, y, w, h = inst.bbox
    adv_em = advance_width_em(inst, proj)
    f = proj.word_scale_factor_for(inst.word_kinds)
    return (
        f"char: {inst.char}   codepoint: U+{inst.codepoint.upper()}\n"
        f"UPEM: {proj.units_per_em}   ascent: {proj.ascent}   descent: {proj.descent}\n"
        f"bbox: x={x} y={y} w={w} h={h} (px)\n"
        f"advance width: {inst.advance_width_px} px  →  {adv_em} em units\n"
        f"word kinds: {word_kinds_labels(inst.word_kinds)}   "
        f"scale factor F={f:.2f}\n"
        f"baseline y: {inst.baseline_y} px   "
        f"baseline offset: {inst.baseline_y - (y + h)} px (descender if <0)\n"
        f"OCR conf: {inst.ocr_conf:.1f}\n"
        f"parts: {inst.part_count}   holes: {inst.hole_count}   "
        f"contour pts: {inst.contour_point_count}\n"
        f"source: {inst.source_image} word#{inst.word_index} sym#{inst.sym_index}"
    )