"""Bottom-right panel: contour rendering for each glyph in a word."""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt
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

    def show_word(self, word: Word | None) -> None:
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
            if not sym.contour_pts:
                continue
            pts = np.array(
                [[px - x + pad, py - y + pad] for px, py in sym.contour_pts],
                dtype=np.int32,
            )
            cv2.polylines(canvas, [pts], True, color, 1)
            cv2.fillPoly(canvas, [pts], color)

        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        self.label.setPixmap(QPixmap.fromImage(qimg).copy())
        self.label.setText("")