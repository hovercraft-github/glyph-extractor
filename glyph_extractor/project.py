"""Project database: an internally managed store of collected glyphs.

A ``Project`` is a single ``project.json`` file that accumulates glyph data
across multiple source images. Each codepoint maps to a list of
``GlyphInstance`` records (one per occurrence seen in a marked word). The user
picks the "best" instance per codepoint (auto-selected by default), and the
selected instances are used to assemble a TrueType font.

The on-disk format is a single JSON document holding everything in
memory-serialized form. This is the simplest format and is fine for the glyph
counts typical of a handwriting font (a few hundred codepoints × a handful of
instances each).
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import BBox, GlyphPart, Symbol, Word


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GlyphInstance:
    """One observed occurrence of a glyph for a given codepoint.

    All geometry is stored in **word-local pixel coordinates** (top-down y),
    with the origin at the word bbox's top-left corner. That is, every
    coordinate has had the source word's ``(x, y)`` offset subtracted from it
    at commit time (see ``add_word_glyphs``). Consequences:

    - ``bbox`` is relative to the word origin: ``(sym.x - word.x, sym.y -
      word.y, w, h)``.
    - ``word_bbox`` is ``(0, 0, word.w, word.h)``.
    - ``baseline_y`` is the baseline offset from the word's top edge
      (``word.baseline_y - word.y``), so it is always within ``[0, word.h]``
      by construction — no clamping is needed downstream.
    - ``parts`` contour points are likewise word-local.

    The vectorization / font-building layer converts these local px to font
    units (flipping y, scaling px → em, positioning relative to baseline).
    """

    codepoint: str                      # hex codepoint, e.g. "430" for "а"
    char: str                           # the actual character
    # Traceability back to the source.
    source_image: str = ""              # basename of the source PNG
    word_index: int = -1
    sym_index: int = -1
    # Geometry (word-local px, top-down y; origin = word bbox top-left).
    bbox: BBox = (0, 0, 0, 0)           # symbol bbox (x, y, w, h), word-local
    word_bbox: BBox = (0, 0, 0, 0)      # (0, 0, word.w, word.h)
    baseline_y: int = 0                 # baseline offset from word top, word-local
    # Contour data (word-local px).
    parts: List[GlyphPart] = field(default_factory=list)
    # Quality signal used for auto-selection.
    ocr_conf: float = 0.0
    # Raster preview stored inline as base64 PNG (small crops).
    preview_png_b64: str = ""
    # Lazily-filled vectorization cache (bezier segments). Not serialized by
    # default; recomputed on demand. See vectorize.py.
    vector_cache: Optional[dict] = None

    # --- Derived metrics (image px) ---

    @property
    def advance_width_px(self) -> int:
        """Natural advance width in pixels = symbol bbox width."""
        return self.bbox[2]

    @property
    def left_bearing_px(self) -> int:
        """Left side bearing in pixels (0 — bbox already starts at x)."""
        return 0

    @property
    def contour_point_count(self) -> int:
        """Total number of contour points across all parts (quality signal)."""
        n = 0
        for part in self.parts:
            n += len(part.outer)
            n += sum(len(h) for h in part.holes)
        return n

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def hole_count(self) -> int:
        return sum(len(p.holes) for p in self.parts)

    def quality_score(self) -> float:
        """Higher is better. Used by auto_select_best.

        Combines OCR confidence with a small bonus for clean contours (more
        parts/holes correctly captured = more structural fidelity) and a
        penalty for tiny or degenerate bboxes.
        """
        score = self.ocr_conf
        # Bonus for capturing holes (e.g. the loop of "а", "о").
        score += 5.0 * self.hole_count
        # Penalty for degenerate boxes.
        w, h = self.bbox[2], self.bbox[3]
        if w < 3 or h < 3:
            score -= 50.0
        # Mild bonus for reasonable contour resolution.
        score += min(self.contour_point_count, 80) * 0.1
        return score


@dataclass
class Project:
    """An in-memory project database that serializes to a single JSON file."""

    name: str = "Untitled"
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)
    # Font metrics (em units). Defaults suit a 1000-upem Latin/Cyrillic font.
    units_per_em: int = 1000
    ascent: int = 800
    descent: int = -200
    # OCR settings: pytesseract recognition language(s). A ``+``-separated list
    # of tesseract language codes (e.g. "rus+eng"). Empty => tesseract default.
    ocr_lang: str = "rus+eng"
    # Path to the tesseract executable. Empty => pytesseract default.
    tesseract_cmd: str = ""
    # codepoint hex -> list of instances.
    glyphs: Dict[str, List[GlyphInstance]] = field(default_factory=dict)
    # codepoint hex -> index into glyphs[codepoint] of the chosen instance.
    selected: Dict[str, int] = field(default_factory=dict)
    # Filesystem location (set on load/save).
    path: Optional[str] = None

    # --- Mutation ---

    def add_instance(self, inst: GlyphInstance) -> None:
        """Append an instance and (re)auto-select the best for its codepoint."""
        cp = inst.codepoint
        if not cp:
            return
        self.glyphs.setdefault(cp, []).append(inst)
        self._auto_select(cp)
        self.modified_at = time.time()

    def add_word_glyphs(
        self,
        word: Word,
        source_image: str = "",
        preview_image=None,
    ) -> int:
        """Commit every symbol of a marked word into the project DB.

        ``preview_image`` is the source image array (used to render inline
        preview PNGs). If None, no preview is stored.

        Returns the number of instances added.
        """
        added = 0
        # Translate all geometry to word-local coordinates (origin = word bbox
        # top-left) at this commit boundary. After this point every stored
        # number is self-contained: baseline_y is within [0, word.h] by
        # construction, so no downstream clamping is needed.
        wx, wy, ww, wh = word.box
        for sym in word.symbols:
            if not sym.char:
                continue
            sx, sy, sw, sh = sym.box
            inst = GlyphInstance(
                codepoint=format(ord(sym.char), "x"),
                char=sym.char,
                source_image=source_image,
                word_index=word.index,
                sym_index=sym.index,
                bbox=(sx - wx, sy - wy, sw, sh),
                word_bbox=(0, 0, ww, wh),
                baseline_y=word.baseline_y - wy,
                parts=[_copy_part_local(p, wx, wy) for p in sym.parts],
                ocr_conf=sym.conf,
            )
            if preview_image is not None:
                inst.preview_png_b64 = _crop_to_b64(preview_image, sym.box)
            self.add_instance(inst)
            added += 1
        return added

    def remove_instance(self, codepoint: str, index: int) -> None:
        instances = self.glyphs.get(codepoint)
        if not instances or not (0 <= index < len(instances)):
            return
        del instances[index]
        if not instances:
            self.glyphs.pop(codepoint, None)
            self.selected.pop(codepoint, None)
        else:
            self._auto_select(codepoint)
        self.modified_at = time.time()

    def set_selected(self, codepoint: str, index: int) -> None:
        instances = self.glyphs.get(codepoint)
        if instances and 0 <= index < len(instances):
            self.selected[codepoint] = index
            self.modified_at = time.time()

    def best_instance(self, codepoint: str) -> Optional[GlyphInstance]:
        instances = self.glyphs.get(codepoint)
        if not instances:
            return None
        idx = self.selected.get(codepoint, 0)
        if not (0 <= idx < len(instances)):
            idx = 0
        return instances[idx]

    def _auto_select(self, codepoint: str) -> None:
        instances = self.glyphs.get(codepoint)
        if not instances:
            self.selected.pop(codepoint, None)
            return
        best_idx = max(range(len(instances)), key=lambda i: instances[i].quality_score())
        self.selected[codepoint] = best_idx

    # --- Read-only summaries ---

    def codepoints(self) -> List[str]:
        """Codepoints sorted by numeric value (not lexicographic hex)."""
        def _key(cp: str) -> int:
            try:
                return int(cp, 16)
            except (ValueError, TypeError):
                return 0
        return sorted(self.glyphs.keys(), key=_key)

    def instance_count(self) -> int:
        return sum(len(v) for v in self.glyphs.values())

    def collection_summary(self) -> Dict[str, int]:
        return {
            "codepoints": len(self.glyphs),
            "instances": self.instance_count(),
        }


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _part_to_dict(part: GlyphPart) -> dict:
    return {
        "outer": [list(p) for p in part.outer],
        "holes": [[list(p) for p in hole] for hole in part.holes],
    }


def _part_from_dict(d: dict) -> GlyphPart:
    return GlyphPart(
        outer=[tuple(p) for p in d.get("outer", [])],
        holes=[[tuple(p) for p in hole] for hole in d.get("holes", [])],
    )


def _instance_to_dict(inst: GlyphInstance) -> dict:
    d = asdict(inst)
    # Replace parts (dataclass -> dict) and drop the non-serializable cache.
    d["parts"] = [_part_to_dict(p) for p in inst.parts]
    d.pop("vector_cache", None)
    # bbox/word_bbox are tuples -> asdict makes them tuples already; ensure
    # they serialize as lists.
    d["bbox"] = list(inst.bbox)
    d["word_bbox"] = list(inst.word_bbox)
    return d


def _instance_from_dict(d: dict) -> GlyphInstance:
    return GlyphInstance(
        codepoint=d["codepoint"],
        char=d.get("char", ""),
        source_image=d.get("source_image", ""),
        word_index=d.get("word_index", -1),
        sym_index=d.get("sym_index", -1),
        bbox=tuple(d.get("bbox", (0, 0, 0, 0))),
        word_bbox=tuple(d.get("word_bbox", (0, 0, 0, 0))),
        baseline_y=d.get("baseline_y", 0),
        parts=[_part_from_dict(p) for p in d.get("parts", [])],
        ocr_conf=d.get("ocr_conf", 0.0),
        preview_png_b64=d.get("preview_png_b64", ""),
        vector_cache=None,
    )


def _project_to_dict(proj: Project) -> dict:
    return {
        "name": proj.name,
        "created_at": proj.created_at,
        "modified_at": proj.modified_at,
        "units_per_em": proj.units_per_em,
        "ascent": proj.ascent,
        "descent": proj.descent,
        "ocr_lang": proj.ocr_lang,
        "tesseract_cmd": proj.tesseract_cmd,
        "glyphs": {
            cp: [_instance_to_dict(i) for i in instances]
            for cp, instances in proj.glyphs.items()
        },
        "selected": dict(proj.selected),
        "format": "glyph-extractor-project",
        "version": 3,
    }


def _project_from_dict(d: dict) -> Project:
    version = d.get("version", 1)
    proj = Project(
        name=d.get("name", "Untitled"),
        created_at=d.get("created_at", time.time()),
        modified_at=d.get("modified_at", time.time()),
        units_per_em=d.get("units_per_em", 1000),
        ascent=d.get("ascent", 800),
        descent=d.get("descent", -200),
        ocr_lang=d.get("ocr_lang", "rus+eng"),
        tesseract_cmd=d.get("tesseract_cmd", ""),
        glyphs={
            cp: [_instance_from_dict(i) for i in instances]
            for cp, instances in d.get("glyphs", {}).items()
        },
        selected=dict(d.get("selected", {})),
    )
    # Migrate older (page-absolute) instances to word-local coordinates.
    # v1/v2 stored geometry in image pixel coordinates; v3 stores it word-local.
    if version < 3:
        for instances in proj.glyphs.values():
            for inst in instances:
                _migrate_instance_to_local(inst)
    # Ensure every codepoint has a valid selection.
    for cp in proj.glyphs:
        if cp not in proj.selected or proj.selected[cp] >= len(proj.glyphs[cp]):
            proj._auto_select(cp)
    return proj


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def create_project(path: str, name: Optional[str] = None) -> Project:
    """Create a new empty project anchored at ``path`` and save it."""
    if name is None:
        name = os.path.splitext(os.path.basename(path))[0] or "Untitled"
    proj = Project(name=name, path=path)
    save_project(proj)
    return proj


def load_project(path: str) -> Project:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    proj = _project_from_dict(data)
    proj.path = path
    return proj


def save_project(proj: Project, path: Optional[str] = None) -> str:
    """Serialize the project to JSON. Returns the written path."""
    if path is not None:
        proj.path = path
    if proj.path is None:
        raise ValueError("Project has no path; provide one to save_project.")
    proj.modified_at = time.time()
    data = _project_to_dict(proj)
    os.makedirs(os.path.dirname(os.path.abspath(proj.path)) or ".", exist_ok=True)
    with open(proj.path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return proj.path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _copy_part(part: GlyphPart) -> GlyphPart:
    return GlyphPart(
        outer=list(part.outer),
        holes=[list(h) for h in part.holes],
    )


def _copy_part_local(part: GlyphPart, dx: int, dy: int) -> GlyphPart:
    """Copy a GlyphPart, translating every point by ``(-dx, -dy)``.

    Used at the commit boundary (``add_word_glyphs``) to convert page-absolute
    contour coordinates into word-local coordinates (origin = word bbox
    top-left).
    """
    return GlyphPart(
        outer=[(px - dx, py - dy) for px, py in part.outer],
        holes=[[(px - dx, py - dy) for px, py in hole] for hole in part.holes],
    )


def _migrate_instance_to_local(inst: GlyphInstance) -> None:
    """Migrate a v2 (page-absolute) instance to v3 (word-local) in place.

    v2 stored ``word_bbox`` in page-absolute coordinates, so its ``(x, y)``
    is the exact offset to subtract from every coordinate. After migration
    ``word_bbox`` becomes ``(0, 0, w, h)``.

    Stale baselines (from projects saved before line-level baseline
    estimation) could land outside ``[0, word.h]``. A baseline above the word
    top (``< 0``) is degenerate and would map the glyph below the em box, so
    it is clamped to the word bottom (``word.h``). A baseline below the word
    bottom (``> word.h``) is the legitimate floating case (quotes,
    diacritics) and is kept as-is.
    """
    ox, oy, ow, oh = inst.word_bbox
    if ox == 0 and oy == 0:
        return  # already local (or no offset)
    sx, sy, sw, sh = inst.bbox
    inst.bbox = (sx - ox, sy - oy, sw, sh)
    inst.word_bbox = (0, 0, ow, oh)
    bl = inst.baseline_y - oy
    # Clamp degenerate baseline (above word top) to word bottom; keep
    # floating baseline (below word bottom) as-is.
    if bl < 0:
        bl = oh
    inst.baseline_y = bl
    for part in inst.parts:
        part.outer = [(px - ox, py - oy) for px, py in part.outer]
        part.holes = [
            [(px - ox, py - oy) for px, py in hole] for hole in part.holes
        ]


def _crop_to_b64(image, box: BBox, pad: int = 1) -> str:
    """Crop ``box`` from ``image`` and return a base64-encoded PNG string."""
    import cv2  # local import to avoid hard dependency at import time

    if image is None:
        return ""
    img_h, img_w = image.shape[:2]
    x, y, w, h = box
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img_w, x + w + pad)
    y1 = min(img_h, y + h + pad)
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return ""
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")