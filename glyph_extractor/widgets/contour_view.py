"""Bottom-right panel: contour rendering for each glyph in a word."""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ..models import Word


# Distinct colors for up to N symbols.
_COLORS = [
    (0, 0, 255),
    (0, 255, 0),
    (255, 0, 0),
    (0, 255, 255),
    (255, 0, 255),
    (255, 255, 0),
    (128, 0, 255),
    (0, 128, 255),
    (128, 255, 0),
    (255, 128, 0),
]


class ContourViewWidget(QWidget):
    """Shows the contours of every glyph in the selected word."""

    zoom_changed = pyqtSignal(float)  # emitted when user scrolls with Ctrl

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.label = QLabel("No word selected")
        self.label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)
        layout.addWidget(self.scroll)

        self._word: Word | None = None
        self._base_pixmap: QPixmap | None = None
        self._base_width: int = 0
        self._zoom: float = 1.0  # user zoom multiplier (shared via main window)

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, zoom)
        self._apply_scale()

    def show_word(self, word: Word | None) -> None:
        self._word = word
        self._render()

    def _render(self) -> None:
        word = self._word
        if word is None:
            self.label.setText("No word selected")
            self.label.setPixmap(QPixmap())
            return

        x, y, w, h = word.box
        pad = 8
        canvas_w = w + 2 * pad
        canvas_h = h + 2 * pad
        canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

        for i, sym in enumerate(word.symbols):
            color = _COLORS[i % len(_COLORS)]
            if not sym.parts:
                continue
            # Render every part (external contour + its holes).
            for part in sym.parts:
                if not part.outer:
                    continue
                pts = np.array(
                    [[px - x + pad, py - y + pad] for px, py in part.outer],
                    dtype=np.int32,
                )
                # Fill the outer contour, then "punch" holes with background color.
                cv2.fillPoly(canvas, [pts], color)
                for hole in part.holes:
                    hole_pts = np.array(
                        [[px - x + pad, py - y + pad] for px, py in hole],
                        dtype=np.int32,
                    )
                    cv2.fillPoly(canvas, [hole_pts], (0, 0, 0))
                cv2.polylines(canvas, [pts], True, color, 1)
                for hole in part.holes:
                    hole_pts = np.array(
                        [[px - x + pad, py - y + pad] for px, py in hole],
                        dtype=np.int32,
                    )
                    cv2.polylines(canvas, [hole_pts], True, color, 1)

        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        self._base_pixmap = QPixmap.fromImage(qimg).copy()
        self._base_width = self._base_pixmap.width()
        self._apply_scale()

    def _apply_scale(self) -> None:
        if self._base_pixmap is None:
            return
        # Base fit: fill ~2/3 of the viewport width for an average-length word.
        viewport_w = max(1, self.scroll.viewport().width())
        base_fit = (viewport_w * 2 / 3) / max(1, self._base_width)
        scale = base_fit * self._zoom
        scaled = self._base_pixmap.scaled(
            max(1, int(self._base_pixmap.width() * scale)),
            max(1, int(self._base_pixmap.height() * scale)),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.label.setPixmap(scaled)
        self.label.setText("")

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y() / 120.0
            new_zoom = self._zoom * (1.15 ** delta)
            self.zoom_changed.emit(new_zoom)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._apply_scale()