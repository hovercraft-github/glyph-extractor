"""Assemble a TrueType font from a Project's selected glyph instances.

Uses ``fontTools.fontBuilder.FontBuilder`` to build a minimal but valid TTF
with head/name/hhea/os2/cmap/glyf/hmtx tables, plus an optional GPOS kern
table computed from the project's glyph instances (reusing the gap-based
kerning logic from ``kerning.py``, adapted to read from the in-memory project
DB instead of a CSV).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont

from .project import GlyphInstance, Project
from .vectorize import advance_width_em, to_tt_glyph, word_scale


# ---------------------------------------------------------------------------
# Glyph naming
# ---------------------------------------------------------------------------


def glyph_name_for_codepoint(cp: str) -> str:
    """Build a fontTools-compatible glyph name from a hex codepoint.

    Uses the ``uniXXXX`` convention for BMP codepoints and ``uXXXXX`` for
    supplementary planes, falling back to a ``gid``-style name for
    non-numeric / empty codepoints.
    """
    try:
        code = int(cp, 16)
    except (ValueError, TypeError):
        return f"gid_{cp}"
    if code <= 0xFFFF:
        return f"uni{code:04X}"
    return f"u{code:05X}"


# ---------------------------------------------------------------------------
# Kerning from the project DB
# ---------------------------------------------------------------------------


def _instance_gap_em(a: GlyphInstance, b: GlyphInstance, proj: Project) -> Optional[float]:
    """Pixel gap between two adjacent instances, scaled to em units.

    Uses contour edges when available, falls back to bbox edges. Returns None
    if the gap can't be computed (missing geometry). The scale is the shared
    composition-normalized :func:`vectorize.word_scale` (cap-height
    reference), so kerning is consistent with the glyph outlines.
    """
    if not a.parts or not b.parts:
        # bbox fallback
        a_right = a.bbox[0] + a.bbox[2]
        b_left = b.bbox[0]
        gap_px = b_left - a_right
    else:
        a_rights = [max((p[0] for p in part.outer), default=a.bbox[0] + a.bbox[2]) for part in a.parts]
        b_lefts = [min((p[0] for p in part.outer), default=b.bbox[0]) for part in b.parts]
        if not a_rights or not b_lefts:
            return None
        gap_px = min(b_lefts) - max(a_rights)
    scale = word_scale(a, proj)
    return gap_px * scale


def compute_kerning_from_project(
    proj: Project, min_samples: int = 1
) -> Dict[Tuple[str, str], int]:
    """Compute average kerning pairs (in em units) from the project DB.

    Iterates over every instance and looks, within the same source word, for
    the instance whose (word_index, sym_index) is the immediate successor.
    This reconstructs adjacency from the stored traceability fields without
    needing the original Word objects.

    Returns {(glyphNameA, glyphNameB): kerning_value_in_font_units}.
    """
    # Index instances by (source_image, word_index) -> list sorted by sym_index.
    by_word: Dict[Tuple[str, int], List[GlyphInstance]] = defaultdict(list)
    for instances in proj.glyphs.values():
        for inst in instances:
            if inst.word_index < 0:
                continue
            by_word[(inst.source_image, inst.word_index)].append(inst)
    for key in by_word:
        by_word[key].sort(key=lambda i: i.sym_index)

    pair_gaps: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for syms in by_word.values():
        for i in range(len(syms) - 1):
            a, b = syms[i], syms[i + 1]
            if a.sym_index + 1 != b.sym_index:
                continue  # not strictly adjacent
            gap = _instance_gap_em(a, b, proj)
            if gap is None:
                continue
            na = glyph_name_for_codepoint(a.codepoint)
            nb = glyph_name_for_codepoint(b.codepoint)
            pair_gaps[(na, nb)].append(gap)

    kerning: Dict[Tuple[str, str], int] = {}
    for pair, gaps in pair_gaps.items():
        if len(gaps) >= min_samples:
            kerning[pair] = int(round(sum(gaps) / len(gaps)))
    return kerning


# ---------------------------------------------------------------------------
# Font assembly
# ---------------------------------------------------------------------------


def build_font(proj: Project, out_path: str, family_name: Optional[str] = None) -> str:
    """Build a TrueType font from the project's selected glyph instances.

    Writes the resulting ``.ttf`` to ``out_path`` and returns it.

    All vectorize caches are invalidated at the start so the font reflects
    any ongoing changes (set-best, delete, re-edit) without requiring a
    save+reopen cycle. The cache is normally cleared on load, but within a
    session it persists and can go stale if the instance's geometry or
    selection changed since it was last viewed.
    """
    if family_name is None:
        family_name = proj.name or "Handwriting"

    # Invalidate all vectorize caches so the font reflects current state.
    for instances in proj.glyphs.values():
        for inst in instances:
            inst.vector_cache = None

    upem = proj.units_per_em
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder([".notdef"] + [glyph_name_for_codepoint(cp) for cp in proj.codepoints()])
    fb.setupCharacterMap(
        {int(cp, 16): glyph_name_for_codepoint(cp) for cp in proj.codepoints() if _is_int(cp)}
    )

    # --- Glyph outlines + metrics ---
    glyphs: Dict[str, object] = {}
    hmetrics: Dict[str, Tuple[int, int]] = {}
    # .notdef: empty glyph with a small advance.
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.endPath()
    glyphs[".notdef"] = pen.glyph()
    hmetrics[".notdef"] = (int(upem * 0.5), 0)

    for cp in proj.codepoints():
        inst = proj.best_instance(cp)
        if inst is None:
            continue
        gname = glyph_name_for_codepoint(cp)
        try:
            glyph = to_tt_glyph(inst, proj, gname)
        except Exception:
            # Fall back to an empty glyph if vectorization fails.
            p = TTGlyphPen(None)
            p.moveTo((0, 0))
            p.endPath()
            glyph = p.glyph()
        glyphs[gname] = glyph
        adv = advance_width_em(inst, proj)
        lsb = 0  # left side bearing already baked into the outline via _px_to_font
        hmetrics[gname] = (adv, lsb)

    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(hmetrics)

    # --- Vertical metrics ---
    fb.setupHorizontalHeader(ascent=proj.ascent, descent=proj.descent)
    fb.setupNameTable(
        {
            "familyName": family_name,
            "styleName": "Regular",
        }
    )
    fb.setupOS2(
        sTypoAscender=proj.ascent,
        sTypoDescender=proj.descent,
        sTypoLineGap=0,
        usWinAscent=proj.ascent,
        usWinDescent=abs(proj.descent),
    )
    fb.setupPost()
    fb.setupHead(unitsPerEm=upem)

    font: TTFont = fb.font

    # --- Kerning (GPOS) ---
    kerning = compute_kerning_from_project(proj)
    if kerning:
        _add_kerning_gpos(font, kerning)

    font.save(out_path)
    return out_path


def _is_int(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except (ValueError, TypeError):
        return False


def _add_kerning_gpos(font: TTFont, kerning: Dict[Tuple[str, str], int]) -> None:
    """Attach a GPOS kern lookup to an already-built font.

    Builds a minimal GPOS table with a single PairPos (Format 2) lookup
    covering the provided kerning pairs.
    """
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import otTables

    gpos = font["GPOS"] = newTable("GPOS")
    gpos.table = otTables.GPOS()
    gpos.table.Version = 0x00010000

    # Script list: default (DFLT) + cyrl + latn, all using the 'kern' feature.
    script_record = otTables.ScriptRecord()
    script_record.ScriptTag = "DFLT"
    script_record.Script = otTables.Script()
    script_record.Script.DefaultLangSys = otTables.DefaultLangSys()
    script_record.Script.DefaultLangSys.ReqFeatureIndex = 0xFFFF
    script_record.Script.DefaultLangSys.FeatureIndex = [0]
    script_record.Script.LangSysRecord = []

    feature_record = otTables.FeatureRecord()
    feature_record.FeatureTag = "kern"
    feature_record.Feature = otTables.Feature()
    feature_record.Feature.LookupListIndex = [0]
    feature_record.Feature.FeatureParams = None

    gpos.table.ScriptList = otTables.ScriptList()
    gpos.table.ScriptList.ScriptRecord = [script_record]
    gpos.table.FeatureList = otTables.FeatureList()
    gpos.table.FeatureList.FeatureRecord = [feature_record]
    gpos.table.FeatureList.FeatureCount = 1

    # Lookup 0: PairPos Format 2 (class-based would need classes; we use
    # Format 1 per-glyph pairs for simplicity and correctness).
    lookup = otTables.Lookup()
    lookup.LookupType = 2  # PairPos
    lookup.LookupFlag = 0
    pair_pos = otTables.PairPos()
    pair_pos.Format = 1
    # ValueFormat1 = XAdvance (0x0001); ValueFormat2 = none (0).
    pair_pos.ValueFormat1 = 0x0001
    pair_pos.ValueFormat2 = 0
    pair_pos.Coverage = otTables.Coverage()
    first_glyphs = sorted({a for (a, _b) in kerning})
    pair_pos.Coverage.Glyph = first_glyphs
    pair_pos.Coverage.Format = 1
    pair_pos.PairSet = []
    for first in first_glyphs:
        pair_set = otTables.PairSet()
        pair_set.PairValueRecord = []
        for (a, b), value in sorted(kerning.items()):
            if a != first:
                continue
            pvr = otTables.PairValueRecord()
            pvr.SecondGlyph = b
            pvr.Value1 = otTables.ValueRecord()
            pvr.Value1.XAdvance = value
            pvr.Value2 = None
            pair_set.PairValueRecord.append(pvr)
        pair_set.PairValueRecord.sort(key=lambda r: r.SecondGlyph)
        pair_pos.PairSet.append(pair_set)
    lookup.SubTable = [pair_pos]
    gpos.table.LookupList = otTables.LookupList()
    gpos.table.LookupList.Lookup = [lookup]
    gpos.table.LookupList.LookupCount = 1