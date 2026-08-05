"""Vectorization: trace glyph contours into bezier curves and build fontTools glyphs.

Two responsibilities:

1. ``vectorize_instance`` — re-rasterize a ``GlyphInstance``'s polygonal
   contour ``parts`` (outer + holes) onto a pixel grid and trace them with
   ``pypotrace`` to obtain cubic bezier segments. The result is a
   ``VectorizedGlyph`` (a list of ``VectorPart``s, each with outer + hole
   bezier curves in **image pixel coordinates**). This is cached on the
   instance's ``vector_cache`` and used by the UI preview.

2. ``to_tt_glyph`` — convert a ``VectorizedGlyph`` into a fontTools
   ``TTGlyph`` by transforming image-pixel coordinates into font units:
   - flip y so that the baseline sits at y=0 (font coords are bottom-up);
   - scale pixels → em units using the word height as the reference for the
     x-height/cap-height band;
   - translate x so the left bearing is applied;
   - emit cubic bezier curves via ``TTGlyphPen``.

The polygonal contours stored in ``GlyphInstance.parts`` are already a good
approximation, but potrace produces smooth, compact cubic beziers which are
far better suited to a TrueType/CFF outline than the raw polyline points.
We re-rasterize the polygons (rather than tracing the original thresholded
image) so that vectorization depends only on the stored contour data and is
deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .letter_kinds import Kind, letter_kinds
from .models import GlyphPart
from .project import GlyphInstance, Project


# ---------------------------------------------------------------------------
# Vectorized representation
# ---------------------------------------------------------------------------


@dataclass
class BezierSegment:
    """One cubic bezier segment or a corner (polyline vertex).

    For a smooth segment: start → c1 → c2 → end (cubic).
    For a corner segment: start → c → end (the corner point ``c`` is the
    control; we store it in ``c1`` and leave ``c2`` None so consumers can
    treat corners as a quadratic/line).
    """

    start: Tuple[float, float]
    c1: Tuple[float, float]
    end: Tuple[float, float]
    c2: Optional[Tuple[float, float]] = None  # None => corner segment

    @property
    def is_corner(self) -> bool:
        return self.c2 is None


@dataclass
class VectorPart:
    """One connected component's bezier curves: an outer loop + hole loops."""

    outer: List[BezierSegment] = field(default_factory=list)
    holes: List[List[BezierSegment]] = field(default_factory=list)
    start_point: Tuple[float, float] = (0.0, 0.0)


@dataclass
class VectorizedGlyph:
    """The full vectorized outline of a glyph, in image pixel coordinates."""

    parts: List[VectorPart] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


def _rasterize_parts(
    parts: List[GlyphPart], bbox: Tuple[int, int, int, int], pad: int = 2
) -> Tuple[np.ndarray, int, int, int, int]:
    """Rasterize polygonal parts onto a binary bitmap for potrace.

    Returns (bitmap, x0, y0, w, h) where the bitmap is uint8 with 1 = ink.
    Coordinates in the bitmap are relative to (x0, y0).
    """
    x, y, w, h = bbox
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = x + w + pad
    y1 = y + h + pad
    cw = x1 - x0
    ch = y1 - y0
    if cw <= 0 or ch <= 0:
        return np.zeros((1, 1), dtype=np.uint8), x0, y0, 1, 1

    import cv2

    # Outer mask: union of all outer contours.
    outer_mask = np.zeros((ch, cw), dtype=np.uint8)
    hole_mask = np.zeros((ch, cw), dtype=np.uint8)
    for part in parts:
        if not part.outer:
            continue
        pts = np.array(
            [[px - x0, py - y0] for px, py in part.outer], dtype=np.int32
        )
        cv2.fillPoly(outer_mask, [pts], (1,))
        for hole in part.holes:
            if not hole:
                continue
            hpts = np.array(
                [[px - x0, py - y0] for px, py in hole], dtype=np.int32
            )
            cv2.fillPoly(hole_mask, [hpts], (1,))
    # Ink = outer minus holes.
    bitmap = np.where(hole_mask.astype(bool), 0, outer_mask).astype(np.uint8)
    return bitmap, x0, y0, cw, ch


def _curve_to_segments(curve) -> Tuple[List[BezierSegment], Tuple[float, float]]:
    """Convert a potrace Curve into a list of BezierSegment + its start point."""
    start = (float(curve.start_point[0]), float(curve.start_point[1]))
    segs: List[BezierSegment] = []
    cur_start = start
    for s in curve.segments:
        end = (float(s.end_point[0]), float(s.end_point[1]))
        if s.is_corner:
            c = (float(s.c[0]), float(s.c[1]))
            segs.append(BezierSegment(start=cur_start, c1=c, end=end, c2=None))
        else:
            c1 = (float(s.c1[0]), float(s.c1[1]))
            c2 = (float(s.c2[0]), float(s.c2[1]))
            segs.append(BezierSegment(start=cur_start, c1=c1, end=end, c2=c2))
        cur_start = end
    return segs, start


def _trace_bitmap(bitmap: np.ndarray, x0: int, y0: int) -> List[VectorPart]:
    """Trace a binary bitmap with potrace and return VectorParts (image coords)."""
    import potrace  # type: ignore[import-not-found]

    if bitmap.sum() == 0:
        return []
    # potrace expects a 2D array; values != 0 are ink.
    bm = potrace.Bitmap(bitmap)
    path = bm.trace()
    parts: List[VectorPart] = []
    for node in path.curves_tree:
        outer_segs, start = _curve_to_segments(node)
        # Offset back to absolute image coordinates.
        outer_segs = [_offset_seg(s, x0, y0) for s in outer_segs]
        abs_start = (start[0] + x0, start[1] + y0)
        holes: List[List[BezierSegment]] = []
        for child in node.children:
            hole_segs, _ = _curve_to_segments(child)
            holes.append([_offset_seg(s, x0, y0) for s in hole_segs])
        parts.append(VectorPart(outer=outer_segs, holes=holes, start_point=abs_start))
    return parts


def _offset_seg(seg: BezierSegment, dx: int, dy: int) -> BezierSegment:
    return BezierSegment(
        start=(seg.start[0] + dx, seg.start[1] + dy),
        c1=(seg.c1[0] + dx, seg.c1[1] + dy),
        end=(seg.end[0] + dx, seg.end[1] + dy),
        c2=(seg.c2[0] + dx, seg.c2[1] + dy) if seg.c2 is not None else None,
    )


def vectorize_instance(inst: GlyphInstance, force: bool = False) -> VectorizedGlyph:
    """Vectorize a GlyphInstance's contour parts into bezier curves.

    Results are cached on ``inst.vector_cache``. Pass ``force=True`` to
    re-trace even if a cache exists.
    """
    if inst.vector_cache is not None and not force:
        cached = inst.vector_cache.get("vectorized")
        if cached is not None:
            return _vectorized_from_cache(cached)
    bitmap, x0, y0, _, _ = _rasterize_parts(inst.parts, inst.bbox)
    parts = _trace_bitmap(bitmap, x0, y0)
    vg = VectorizedGlyph(parts=parts)
    inst.vector_cache = {"vectorized": _vectorized_to_cache(vg)}
    return vg


# --- cache (de)serialization (JSON-friendly) ---


def _seg_to_dict(s: BezierSegment) -> dict:
    d = {"start": list(s.start), "c1": list(s.c1), "end": list(s.end)}
    if s.c2 is not None:
        d["c2"] = list(s.c2)
    return d


def _seg_from_dict(d: dict) -> BezierSegment:
    return BezierSegment(
        start=tuple(d["start"]),
        c1=tuple(d["c1"]),
        end=tuple(d["end"]),
        c2=tuple(d["c2"]) if "c2" in d else None,
    )


def _vectorized_to_cache(vg: VectorizedGlyph) -> dict:
    return {
        "parts": [
            {
                "start_point": list(p.start_point),
                "outer": [_seg_to_dict(s) for s in p.outer],
                "holes": [[_seg_to_dict(s) for s in hole] for hole in p.holes],
            }
            for p in vg.parts
        ]
    }


def _vectorized_from_cache(d: dict) -> VectorizedGlyph:
    parts = []
    for pd in d.get("parts", []):
        parts.append(
            VectorPart(
                start_point=tuple(pd.get("start_point", (0.0, 0.0))),
                outer=[_seg_from_dict(s) for s in pd.get("outer", [])],
                holes=[[_seg_from_dict(s) for s in hole] for hole in pd.get("holes", [])],
            )
        )
    return VectorizedGlyph(parts=parts)


# ---------------------------------------------------------------------------
# Font-unit conversion + TTGlyph construction
# ---------------------------------------------------------------------------


def _kind_target_em(inst: GlyphInstance, proj: Project, cap_height_em: float) -> Optional[float]:
    """Target em height of this glyph's top reference line, or None.

    Returns the em height (above the baseline) that the glyph's *own* top
    should be normalized to, based on the letter kind(s) of its character:

    - CAPITAL (letters + digits) → ``cap_height_em`` (capitals line).
    - ASCENDER (lowercase ascenders) → ``ascender_ratio * cap_height_em``
      (ascenders line).
    - REGULAR (non-ascender lowercase) → ``regular_ratio * cap_height_em``
      (regulars line).
    - DESCENDER (lowercase descenders: g, j, p, q, y, у, р, д, ц, щ, …) →
      ``regular_ratio * cap_height_em`` (regulars line). A descender's top
      sits at the x-height just like a regular letter; only its bottom
      drops below the baseline. So its *top* is normalized to the regulars
      line, and its descender depth follows proportionally.
    - SPECIAL / FLOATING / UNKNOWN → ``None`` (leave as-is: use the
      word-level composition scale).

    For dual-kind glyphs (e.g. Cyrillic ``ф`` = ascender + descender) the
    *top* kind wins, since we normalize the top to its reference line.
    """
    kinds = letter_kinds(inst.char, proj.ascender_set(), proj.descender_set())
    # Prefer the top-extent kind for normalization (the descender part is
    # below the baseline and does not define the top).
    if Kind.CAPITAL in kinds:
        return cap_height_em
    if Kind.ASCENDER in kinds:
        ratio = proj.kind_ratios.get(Kind.ASCENDER.value, 0.8)
        return ratio * cap_height_em
    if Kind.REGULAR in kinds or Kind.DESCENDER in kinds:
        # Regulars and descenders both sit at the x-height on top; a
        # descender's bottom drops below the baseline, but its top is
        # normalized to the regulars line.
        ratio = proj.kind_ratios.get(Kind.REGULAR.value, 0.6)
        return ratio * cap_height_em
    return None


# Letter kinds that sit ON the baseline (their bbox bottom = baseline).
# Used to decide whether "Put on baseline" is safe to apply automatically.
# Descenders are excluded (their bottom drops below the baseline); special
# symbols and unknown/punctuation are excluded (they are not letters).
_BASELINE_ANCHORING_KINDS = {Kind.CAPITAL, Kind.ASCENDER, Kind.REGULAR}


def _is_baseline_anchoring_letter(inst: GlyphInstance, proj: Project) -> bool:
    """True if the glyph is a non-descender letter that sits on the baseline.

    Capitals, ascenders, and regulars have their bbox bottom on the
    baseline, so "Put on baseline" is always correct for them. Capital+
    descender letters (e.g. Cyrillic Ц, Щ, Д) and plain descenders have a
    bottom that extends below the baseline, so they are excluded. Special
    symbols, floating punctuation, and unknown/punctuation glyphs are also
    excluded (they are not letters).
    """
    kinds = proj.letter_kinds_for(inst.char)
    if Kind.DESCENDER in kinds:
        return False
    return bool(kinds & _BASELINE_ANCHORING_KINDS)


def _compute_scale(inst: GlyphInstance, proj: Project, cap_height_em: float = 700.0) -> float:
    """Pixels → em units, normalized per the glyph's own letter kind.

    Two strategies, selected by the glyph's letter kind:

    **Per-kind normalization** (capitals, ascenders, regulars): the glyph's
    own top (``baseline_y - bbox.y`` px above the baseline) is mapped to its
    kind's reference line in em units:

        scale = target_em / glyph_top_px

    where ``target_em`` is ``cap_height_em`` (capitals), ``ascender_ratio *
    cap_height_em`` (ascenders), or ``regular_ratio * cap_height_em``
    (regulars). This makes each glyph's top touch its reference line
    regardless of the accidental vertical composition of the source word.

    For **non-descender letters** the "Put on baseline" action
    (``inst.put_on_baseline``) is applied first: a non-descender's bbox
    bottom *is* the baseline by definition, so anchoring there corrects
    per-word baseline estimation error. This reuses the same mechanism as
    the browser's "Put on baseline" button. Descenders keep their stored
    line-aware baseline (their bottom drops below it).

    **Word-level fallback** (descenders, special, floating, unknown): the
    per-word scale factor ``F`` (see ``Project.word_scale_factor_for``)
    expresses the source word's vertical extent as a multiple of the cap
    height. Dividing the word bbox height by ``F`` recovers the
    composition-independent cap height in pixels, mapped to ``cap_height_em``:

        scale = cap_height_em * F / word_h

    The computed scale is cached on ``inst.scale_factor`` for reference
    (shown in the browser metrics and serialized to the DB).
    """
    # Non-descenders: anchor the baseline to the glyph's bbox bottom (the
    # "Put on baseline" action). Reuses inst.put_on_baseline so the effect
    # is identical to the browser button and original_baseline_y is
    # preserved for "Re-vectorize" to restore.
    if _is_baseline_anchoring_letter(inst, proj):
        inst.put_on_baseline()

    # Per-kind normalization: map the glyph's own top to its reference line.
    target_em = _kind_target_em(inst, proj, cap_height_em)
    if target_em is not None:
        bx, by, bw, bh = inst.bbox
        glyph_top_px = inst.baseline_y - by
        if glyph_top_px > 0:
            scale = target_em / glyph_top_px
            inst.scale_factor = scale
            return scale
        # Degenerate (top at/below baseline): fall through to word-level.

    # Word-level composition fallback.
    word_h = inst.word_bbox[3] if inst.word_bbox[3] > 0 else inst.bbox[3]
    f = proj.word_scale_factor_for(inst.word_kinds)
    scale = cap_height_em * f / max(word_h, 1)
    inst.scale_factor = scale
    return scale


def word_scale(inst: GlyphInstance, proj: Project, cap_height_em: float = 700.0) -> float:
    """Shared px→em scale for a glyph instance (see :func:`_compute_scale`).

    Single source of truth used by vectorization, advance-width, preview,
    font-builder kerning, and the CSV kerning utility so that every consumer
    applies the identical composition-normalized scale.
    """
    return _compute_scale(inst, proj, cap_height_em)


def _px_to_font(
    px: float,
    py: float,
    inst: GlyphInstance,
    scale: float,
    left_bearing_em: int,
) -> Tuple[float, float]:
    """Transform a word-local pixel point to font units.

    Geometry is stored word-local (origin = word bbox top-left), so no
    baseline clamping is needed: ``inst.baseline_y`` is within ``[0, word.h]``
    by construction (see ``add_word_glyphs``).

    - x: (px - bbox.x) * scale + left_bearing_em
    - y: (baseline_y - py) * scale   (flip y; baseline → 0)
    """
    bx, by, _, _ = inst.bbox
    fx = (px - bx) * scale + left_bearing_em
    fy = (inst.baseline_y - py) * scale
    return fx, fy


def to_tt_glyph(inst: GlyphInstance, proj: Project, glyph_name: str):
    """Build a fontTools TTGlyph for the instance's vectorized outline.

    Uses ``vectorize_instance`` (cached) and a ``TTGlyphPen`` wrapped in a
    ``Cu2QuPen`` so the cubic bezier curves produced by potrace are converted
    to the quadratic curves required by the TrueType ``glyf`` table. Corner
    segments are emitted directly as quadratic curves through the corner
    point.

    Geometry is word-local (see ``GlyphInstance``), so ``inst.baseline_y`` is
    within ``[0, word.h]`` by construction — no baseline clamping is needed.
    """
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    vg = vectorize_instance(inst)
    scale = _compute_scale(inst, proj)
    # Left bearing: a small margin so the leftmost ink isn't flush against 0.
    left_bearing_em = int(round(0.05 * proj.units_per_em))

    # Max error for cubic→quadratic conversion, in font units. 1 em unit is
    # visually imperceptible at typical rendering sizes.
    max_err = max(1, proj.units_per_em / 1000.0)

    tt_pen = TTGlyphPen(None)
    pen = Cu2QuPen(tt_pen, max_err=max_err)
    if not vg.parts:
        pen.moveTo((0, 0))
        pen.endPath()
        return tt_pen.glyph()

    for part in vg.parts:
        if not part.outer:
            continue
        # Outer contour: closed.
        start_fx, start_fy = _px_to_font(
            part.start_point[0], part.start_point[1], inst, scale, left_bearing_em
        )
        pen.moveTo((int(round(start_fx)), int(round(start_fy))))
        for seg in part.outer:
            _emit_segment(pen, seg, inst, scale, left_bearing_em)
        pen.closePath()
        # Holes: closed contours drawn in the opposite winding (potrace already
        # gives holes the reverse orientation; we just emit them as separate
        # closed subpaths — TTGlyphPen handles composite glyphs via subpaths).
        for hole in part.holes:
            if not hole:
                continue
            hstart = hole[0].start
            hsx, hsy = _px_to_font(hstart[0], hstart[1], inst, scale, left_bearing_em)
            pen.moveTo((int(round(hsx)), int(round(hsy))))
            for seg in hole:
                _emit_segment(pen, seg, inst, scale, left_bearing_em)
            pen.closePath()
    return tt_pen.glyph()


def _emit_segment(
    pen, seg: BezierSegment, inst: GlyphInstance, scale: float, lb: int
) -> None:
    """Emit one bezier/corner segment to a fontTools pen (in font units)."""
    if seg.is_corner:
        # Corner: line to the corner point, then line to end. We approximate
        # with a single quadratic through the corner.
        cx, cy = _px_to_font(seg.c1[0], seg.c1[1], inst, scale, lb)
        ex, ey = _px_to_font(seg.end[0], seg.end[1], inst, scale, lb)
        pen.qCurveTo(
            (int(round(cx)), int(round(cy))),
            (int(round(ex)), int(round(ey))),
        )
    else:
        c1x, c1y = _px_to_font(seg.c1[0], seg.c1[1], inst, scale, lb)
        c2 = seg.c2
        assert c2 is not None  # guaranteed by is_corner == False
        c2x, c2y = _px_to_font(c2[0], c2[1], inst, scale, lb)
        ex, ey = _px_to_font(seg.end[0], seg.end[1], inst, scale, lb)
        # TTGlyphPen accepts cubic curves; it will convert to quadratics.
        pen.curveTo(
            (int(round(c1x)), int(round(c1y))),
            (int(round(c2x)), int(round(c2y))),
            (int(round(ex)), int(round(ey))),
        )


def advance_width_em(inst: GlyphInstance, proj: Project, side_bearing_em: int = 50) -> int:
    """Compute the advance width in em units for an instance."""
    scale = _compute_scale(inst, proj)
    w_em = int(round(inst.bbox[2] * scale))
    return max(1, w_em + side_bearing_em)


# ---------------------------------------------------------------------------
# UPEM-normalized vectorization (for preview + consistent font units)
# ---------------------------------------------------------------------------


def normalize_to_upem(inst: GlyphInstance, proj: Project) -> VectorizedGlyph:
    """Return a VectorizedGlyph with all coordinates in font units (UPEM).

    Transform applied to every point:
    - x: (px - bbox.x) * scale + left_bearing
    - y: (baseline_y - py) * scale   (flip y; baseline → 0)

    where ``scale`` maps the word-height band to ~700 em units (see
    ``_compute_scale``) and ``left_bearing`` is a small margin. The result is
    a glyph positioned exactly as it will appear in the font, suitable for
    previewing inside an em box.

    Geometry is word-local (see ``GlyphInstance``), so ``inst.baseline_y`` is
    within ``[0, word.h]`` by construction — no baseline clamping is needed,
    and the preview matches the built font exactly.
    """
    vg = vectorize_instance(inst)
    scale = _compute_scale(inst, proj)
    lb = int(round(0.05 * proj.units_per_em))

    def tx(px: float, py: float) -> Tuple[float, float]:
        return _px_to_font(px, py, inst, scale, lb)

    norm_parts: List[VectorPart] = []
    for part in vg.parts:
        nstart = tx(part.start_point[0], part.start_point[1])
        nouter = [_norm_seg(s, tx) for s in part.outer]
        nholes = [[_norm_seg(s, tx) for s in hole] for hole in part.holes]
        norm_parts.append(VectorPart(outer=nouter, holes=nholes, start_point=nstart))
    return VectorizedGlyph(parts=norm_parts)


def _norm_seg(seg: BezierSegment, tx) -> BezierSegment:
    s = tx(seg.start[0], seg.start[1])
    c1 = tx(seg.c1[0], seg.c1[1])
    e = tx(seg.end[0], seg.end[1])
    c2 = tx(seg.c2[0], seg.c2[1]) if seg.c2 is not None else None
    return BezierSegment(start=s, c1=c1, end=e, c2=c2)