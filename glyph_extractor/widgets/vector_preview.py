"""Widget that renders a UPEM-normalized glyph inside an em box.

The glyph's bezier curves are given in **font units** (y-up, baseline at 0,
scaled to the project's units-per-em). The widget draws:
- the em box as a light background (from descent to ascent, width = advance),
- a green baseline line at y = 0,
- the glyph outline filled in dark gray with holes punched out.

Used inside the glyph browser to preview exactly how the glyph will appear in
the assembled font.
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

from ..vectorize import VectorizedGlyph


class VectorPreviewWidget(QWidget):
    """Renders a UPEM-normalized VectorizedGlyph inside its em box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 200)
        # Normalized glyph (font units, y-up, baseline=0).
        self._vg: Optional[VectorizedGlyph] = None
        # Em-box metrics in font units.
        self._upem: int = 1000
        self._ascent: int = 800
        self._descent: int = -200
        self._advance: int = 600  # glyph advance width in font units

    def set_glyph(
        self,
        vg: Optional[VectorizedGlyph],
        upem: int = 1000,
        ascent: int = 800,
        descent: int = -200,
        advance: int = 600,
    ) -> None:
        """Set the normalized glyph + em-box metrics and repaint.

        All geometry is in font units (y-up, baseline at 0).
        """
        self._vg = vg
        self._upem = max(1, upem)
        self._ascent = ascent
        self._descent = descent
        self._advance = max(1, advance)
        self.update()

    def clear(self) -> None:
        self._vg = None
        self.update()

    # --- Painting ---

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Em box in font units: x in [0, advance], y in [descent, ascent].
        box_w = self._advance
        box_h = self._ascent - self._descent  # total em-box height
        if box_w <= 0 or box_h <= 0:
            painter.fillRect(self.rect(), QColor(245, 245, 245))
            return

        # Fit the em box into the widget with padding, preserving aspect ratio.
        pad = 16
        avail_w = max(1, self.width() - 2 * pad)
        avail_h = max(1, self.height() - 2 * pad)
        scale = min(avail_w / box_w, avail_h / box_h)
        draw_w = box_w * scale
        draw_h = box_h * scale
        ox = pad + (avail_w - draw_w) / 2
        # Center vertically.
        oy = pad + (avail_h - draw_h) / 2

        # --- Em-box background ---
        em_rect_color = QColor(230, 230, 230)
        painter.fillRect(
            int(ox), int(oy), int(draw_w) + 1, int(draw_h) + 1, em_rect_color
        )
        # Border around the em box.
        painter.setPen(QPen(QColor(160, 160, 160), 1))
        painter.drawRect(int(ox), int(oy), int(draw_w), int(draw_h))

        # Map a font-unit point (x, y) to widget pixels.
        # Font y is up; widget y is down. Baseline (y=0) sits at
        # oy + ascent*scale (i.e. descent's worth of space below it).
        baseline_screen_y = oy + self._ascent * scale

        def to_screen(fx: float, fy: float) -> QPointF:
            return QPointF(ox + fx * scale, baseline_screen_y - fy * scale)

        # --- Baseline (green) ---
        pen = QPen(QColor(0, 170, 0), 1)
        painter.setPen(pen)
        painter.drawLine(
            int(ox), int(baseline_screen_y),
            int(ox + draw_w), int(baseline_screen_y),
        )

        # --- Glyph outline ---
        if self._vg is None or not self._vg.parts:
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(self.rect(), Qt.AlignCenter, "No vector data")
            return

        for part in self._vg.parts:
            if not part.outer:
                continue
            outer_poly = _segments_to_polygon(part.outer, to_screen)
            if outer_poly.size() < 2:
                continue
            painter.setBrush(QColor(60, 60, 60))
            painter.setPen(QPen(QColor(20, 20, 20), 1))
            painter.drawPolygon(outer_poly)
            for hole in part.holes:
                hpoly = _segments_to_polygon(hole, to_screen)
                if hpoly.size() < 2:
                    continue
                painter.setBrush(em_rect_color)
                painter.setPen(Qt.NoPen)
                painter.drawPolygon(hpoly)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _segments_to_polygon(segments, to_screen) -> QPolygonF:
    """Flatten a closed list of bezier segments into a polygon for drawing.

    Samples each segment with a fixed number of steps (good enough for a
    preview; the actual font outline keeps the true beziers).
    """
    poly = QPolygonF()
    if not segments:
        return poly
    steps = 10
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