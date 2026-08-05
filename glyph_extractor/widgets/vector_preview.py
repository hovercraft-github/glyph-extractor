"""Widget that renders a UPEM-normalized glyph inside an em box.

The glyph's bezier curves are given in **font units** (y-up, baseline at 0,
scaled to the project's units-per-em). The widget draws:
- the em box as a light background (from descent to ascent, width = advance),
- a green baseline line at y = 0,
- per-kind reference lines (one per ``Project.kind_ratios`` entry) showing
  the calibrated vertical extent of each letter kind in em units, each in a
  distinct color with its em value labeled on the left,
- the glyph outline filled in dark gray with holes punched out.

Used inside the glyph browser to preview exactly how the glyph will appear in
the assembled font.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
)
from PyQt5.QtWidgets import QWidget

from ..letter_kinds import CAPITAL_ASCENDER_KEY, DEFAULT_KIND_RATIOS, Kind
from ..vectorize import VectorizedGlyph

# Distinct color per kind for the reference lines. Order matches the
# DEFAULT_KIND_RATIOS iteration order (capital, ascender, regular,
# special, descender).
_KIND_COLORS: Dict[str, QColor] = {
    Kind.CAPITAL.value: QColor(0, 90, 170),    # blue
    Kind.ASCENDER.value: QColor(170, 80, 0),   # orange-brown
    Kind.REGULAR.value: QColor(130, 0, 170),   # purple
    Kind.SPECIAL.value: QColor(0, 130, 130),   # teal
    Kind.DESCENDER.value: QColor(170, 0, 0),   # red
    CAPITAL_ASCENDER_KEY: QColor(0, 160, 0),   # green (above capitals line)
}

# Kinds whose ratio is a *top* extent (above the baseline) vs a *bottom*
# extent (below the baseline). Mirrors letter_kinds._TOP_KINDS/_BOTTOM_KINDS.
_TOP_KINDS = {Kind.CAPITAL, Kind.ASCENDER, Kind.REGULAR, Kind.SPECIAL}
# Synthetic string ratio keys (not Kind enum values) that are top extents.
_TOP_RATIO_KEYS = {CAPITAL_ASCENDER_KEY}
_BOTTOM_KINDS = {Kind.DESCENDER}


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
        # Per-kind extent ratios (cap-relative) + the cap-height reference
        # in em units. Used to draw the reference lines. When empty, no
        # reference lines are drawn.
        self._kind_ratios: Dict[str, float] = dict(DEFAULT_KIND_RATIOS)
        self._cap_height_em: float = 700.0

    def set_glyph(
        self,
        vg: Optional[VectorizedGlyph],
        upem: int = 1000,
        ascent: int = 800,
        descent: int = -200,
        advance: int = 600,
        kind_ratios: Optional[Dict[str, float]] = None,
        cap_height_em: float = 700.0,
    ) -> None:
        """Set the normalized glyph + em-box metrics and repaint.

        All geometry is in font units (y-up, baseline at 0).

        ``kind_ratios`` (cap-relative extents, see ``Project.kind_ratios``)
        and ``cap_height_em`` control the per-kind reference lines: each
        ratio maps to an em height of ``ratio * cap_height_em`` above the
        baseline (top kinds) or below it (descender), drawn in a distinct
        color with its em value labeled on the left.
        """
        self._vg = vg
        self._upem = max(1, upem)
        self._ascent = ascent
        self._descent = descent
        self._advance = max(1, advance)
        self._kind_ratios = dict(kind_ratios) if kind_ratios else dict(DEFAULT_KIND_RATIOS)
        self._cap_height_em = max(1.0, cap_height_em)
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

        # --- Per-kind reference lines ---
        # Each ratio maps to an em height of ratio * cap_height_em. Top
        # kinds are drawn above the baseline; the descender is drawn below.
        # The em value is labeled on the left of each line.
        self._draw_kind_lines(painter, ox, draw_w, baseline_screen_y, scale)

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

    # --- Internal: reference lines ---

    def _draw_kind_lines(
        self,
        painter: QPainter,
        ox: float,
        draw_w: float,
        baseline_screen_y: float,
        scale: float,
    ) -> None:
        """Draw one horizontal reference line per kind-ratio entry.

        Each line sits at ``y = ratio * cap_height_em`` em units above the
        baseline (top kinds) or below it (descender), spanning the em-box
        width. The em value is labeled on the left, in the line's color.
        Lines are clipped to the em box; kinds whose line falls outside
        [descent, ascent] are skipped.
        """
        if not self._kind_ratios:
            return
        label_font = QFont("SansSerif", 7)
        painter.setFont(label_font)
        x_left = int(ox)
        x_right = int(ox + draw_w)
        # Order: top kinds (descending height) first, then descender, so
        # labels don't overlap as badly.
        def _sort_key(kv):
            # Synthetic string keys (e.g. capital_ascender) are top kinds.
            if kv[0] in _TOP_RATIO_KEYS:
                return (0, -kv[1])
            try:
                return (0 if Kind(kv[0]) in _TOP_KINDS else 1, -kv[1])
            except ValueError:
                return (1, -kv[1])

        ordered = sorted(self._kind_ratios.items(), key=_sort_key)
        # Track label y positions to nudge overlapping labels apart.
        used_label_ys: list[int] = []
        for kind, ratio in ordered:
            is_ratio_key = kind in _TOP_RATIO_KEYS
            if not is_ratio_key:
                try:
                    k = Kind(kind)
                except ValueError:
                    continue
            em = ratio * self._cap_height_em
            if is_ratio_key or k in _TOP_KINDS:
                fy = em
            elif k in _BOTTOM_KINDS:
                fy = -em
            else:
                continue
            # Skip if outside the em box.
            if fy > self._ascent or fy < self._descent:
                continue
            screen_y = int(baseline_screen_y - fy * scale)
            color = _KIND_COLORS.get(kind, QColor(120, 120, 120))
            painter.setPen(QPen(color, 1, Qt.DashLine))
            painter.drawLine(x_left, screen_y, x_right, screen_y)
            # Label: "<kind> <em>em" on the left, nudged off overlapping.
            label = f"{kind} {em:.0f}em"
            fm = painter.fontMetrics()
            lw = fm.horizontalAdvance(label)
            lh = fm.height()
            ly = screen_y - lh // 2 + fm.ascent() // 2
            # Nudge so labels don't stack on the same pixel.
            while any(abs(ly - uy) < lh - 2 for uy in used_label_ys):
                ly += lh - 2
            used_label_ys.append(ly)
            # Draw a small background pad for readability.
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 200))
            painter.drawRect(x_left + 1, ly - fm.ascent() + 1, lw + 4, lh)
            painter.setPen(color)
            painter.drawText(x_left + 3, ly, label)


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