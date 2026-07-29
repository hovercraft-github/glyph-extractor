"""Upper-right panel: image fragment with per-symbol bounding boxes."""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ..models import Word


class SymbolViewWidget(QWidget):
    """Shows the cropped word image fragment with bounding boxes around symbols."""

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

        self._image: np.ndarray | None = None
        self._word: Word | None = None
        self._base_pixmap: QPixmap | None = None
        self._base_width: int = 0
        self._zoom: float = 1.0  # user zoom multiplier (shared via main window)

    def set_image(self, image: np.ndarray) -> None:
        self._image = image

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, zoom)
        self._render()

    def show_word(self, word: Word | None) -> None:
        self._word = word
        self._render()

    def _render(self) -> None:
        word = self._word
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

        # Draw bounding boxes around each symbol (red, thicker), offset to fragment coords.
        for sym in word.symbols:
            sx, sy, sw, sh = sym.box
            rx = sx - x0
            ry = sy - y0
            cv2.rectangle(fragment, (rx, ry), (rx + sw, ry + sh), (0, 0, 255), 2)

        # Convert BGR -> RGB for Qt.
        rgb = cv2.cvtColor(fragment, cv2.COLOR_BGR2RGB)
        qimg = QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888
        )
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