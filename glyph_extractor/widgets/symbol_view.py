"""Upper-right panel: image fragment with per-symbol bounding boxes."""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ..models import Word


class SymbolViewWidget(QWidget):
    """Shows the cropped word image fragment with bounding boxes around symbols."""

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

        self._image: np.ndarray | None = None

    def set_image(self, image: np.ndarray) -> None:
        self._image = image

    def show_word(self, word: Word | None) -> None:
        if word is None or self._image is None:
            self.label.setText("No word selected")
            self.label.setPixmap(QPixmap())
            return

        x, y, w, h = word.box
        img_h, img_w = self._image.shape[:2]
        pad = 8
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(img_w, x + w + pad)
        y1 = min(img_h, y + h + pad)
        fragment = self._image[y0:y1, x0:x1].copy()

        # Draw bounding boxes around each symbol (green), offset to fragment coords.
        for sym in word.symbols:
            sx, sy, sw, sh = sym.box
            rx = sx - x0
            ry = sy - y0
            cv2.rectangle(fragment, (rx, ry), (rx + sw, ry + sh), (0, 255, 0), 1)

        # Convert BGR -> RGB for Qt.
        rgb = cv2.cvtColor(fragment, cv2.COLOR_BGR2RGB)
        qimg = QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888
        )
        self.label.setPixmap(QPixmap.fromImage(qimg).copy())
        self.label.setText("")