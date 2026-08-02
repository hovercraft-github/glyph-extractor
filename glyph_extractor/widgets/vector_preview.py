"""Widget that renders a VectorizedGlyph's bezier curves onto a QPainter canvas.

Used inside the glyph browser to preview the vectorized outline of a glyph
instance. The curves are drawn in image-pixel coordinates, auto-fit to the
widget size, with the baseline shown as a horizontal reference line.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import (
    QColor,
    QPainter,
    QPen,
    QPolygonF,
)
from PyQt5.QtWidgets import QWidget

from ..vectorize import BezierSegment, VectorizedGlyph


class VectorPreviewWidget(QWidget):
    """Renders a VectorizedGlyph (bezier outlines) scaled to fit the widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self._vg: Optional[VectorizedGlyph] = None
        self._baseline_y: Optional[float] = None  # image px (top-down)
        # Bounding box of the glyph in image px, used for fitting.
        self._bbox = (0.0, 0.0, 1.0, 1.0)

    def set_glyph(self, vg: Optional[VectorizedGlyph], baseline_y: Optional[float] = None) -> None:
        self._vg = vg
        self._baseline_y = baseline_y
        if vg is not None:
            self._bbox = _compute_bbox(vg)
        self.update()

    def clear(self) -> None:
        self._vg = None
        self._baseline_y = None
        self.update()

    # --- Painting ---

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(245, 245, 245))

        if self._vg is None or not self._vg.parts:
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(self.rect(), Qt.AlignCenter, "No vector data")
            return

        # Compute scale + offset to fit the glyph bbox into the widget with padding.
        pad = 12
        w = max(1, self.width() - 2 * pad)
        h = max(1, self.height() - 2 * pad)
        bx, by, bw, bh = self._bbox
        if bw <= 0 or bh <= 0:
            return
        scale = min(w / bw, h / bh)
        # Center horizontally; align baseline near the bottom third.
        ox = pad + (w - bw * scale) / 2 - bx * scale
        # Map image top-down y to widget top-down y (no flip here — preview is
        # a direct rendering of the image-space outline).
        oy = pad + (h - bh * scale) / 2 - by * scale

        def to_screen(px: float, py: float) -> QPointF:
            return QPointF(ox + px * scale, oy + py * scale)

        # Baseline reference line.
        if self._baseline_y is not None:
            by_screen = oy + self._baseline_y * scale
            pen = QPen(QColor(0, 120, 215, 180), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(0, int(by_screen), self.width(), int(by_screen))

        # Draw each part: filled outer + punched holes, then outline strokes.
        for part in self._vg.parts:
            if not part.outer:
                continue
            # Build the outer polygon by sampling bezier segments.
            outer_poly = _segments_to_polygon(part.outer, to_screen)
            if outer_poly.size() < 2:
                continue
            painter.setBrush(QColor(60, 60, 60))
            painter.setPen(QPen(QColor(20, 20, 20), 1))
            painter.drawPolygon(outer_poly)
            # Punch holes by drawing them with the background color.
            for hole in part.holes:
                hpoly = _segments_to_polygon(hole, to_screen)
                if hpoly.size() < 2:
                    continue
                painter.setBrush(self.palette().window().color())
                painter.setPen(Qt.NoPen)
                painter.drawPolygon(hpoly)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_bbox(vg: VectorizedGlyph):
    xs = []
    ys = []
    for part in vg.parts:
        for seg in part.outer:
            xs.extend([seg.start[0], seg.end[0], seg.c1[0]])
            ys.extend([seg.start[1], seg.end[1], seg.c1[1]])
            if seg.c2 is not None:
                xs.append(seg.c2[0])
                ys.append(seg.c2[1])
        for hole in part.holes:
            for seg in hole:
                xs.extend([seg.start[0], seg.end[0], seg.c1[0]])
                ys.extend([seg.start[1], seg.end[1], seg.c1[1]])
                if seg.c2 is not None:
                    xs.append(seg.c2[0])
                    ys.append(seg.c2[1])
    if not xs:
        return (0.0, 0.0, 1.0, 1.0)
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def _segments_to_polygon(segments, to_screen) -> QPolygonF:
    """Flatten a closed list of bezier segments into a polygon for drawing.

    Samples each segment with a fixed number of steps (good enough for a
    preview; the actual font outline keeps the true beziers).
    """
    poly = QPolygonF()
    if not segments:
        return poly
    steps = 8
    poly.append(to_screen(segments[0].start[0], segments[0].start[1]))
    for seg in segments:
        if seg.is_corner:
            poly.append(to_screen(seg.c1[0], seg.c1[1]))
            poly.append(to_screen(seg.end[0], seg.end[1]))
        else:
            x0, y0 = seg.start
            x1, y1 = seg.c1
            x2, y2 = seg.c2
            x3, y3 = seg.end
            for i in range(1, steps + 1):
                t = i / steps
                mt = 1 - t
                x = mt * mt * mt * x0 + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t * t * t * x3
                y = mt * mt * mt * y0 + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t * t * t * y3
                poly.append(to_screen(x, y))
    return poly