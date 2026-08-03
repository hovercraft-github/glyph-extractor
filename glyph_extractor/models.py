"""Data models for words and symbols."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


BBox = Tuple[int, int, int, int]  # (x, y, w, h) in image coordinates


@dataclass
class GlyphPart:
    """A single connected component of a glyph.

    A glyph may consist of multiple parts (e.g. the dot and body of 'i').
    Each part has one outer contour and zero or more hole contours.
    """

    outer: List[Tuple[int, int]] = field(default_factory=list)
    holes: List[List[Tuple[int, int]]] = field(default_factory=list)


@dataclass
class Symbol:
    """A single glyph within a word."""

    index: int
    char: str                       # current label (editable via word edit)
    suggested_char: str             # original tesseract character
    box: BBox                       # tesseract box in image coords (x, y, w, h)
    conf: float                     # tesseract confidence for this symbol
    parts: List[GlyphPart] = field(default_factory=list)
    preview_path: str = ""

    # --- Backward-compatible properties (derived from parts) ---

    @property
    def contour_pts(self) -> List[Tuple[int, int]]:
        """Outer contour of the first part (for backward compatibility)."""
        if self.parts:
            return self.parts[0].outer
        return []

    @contour_pts.setter
    def contour_pts(self, value: List[Tuple[int, int]]) -> None:
        if not self.parts:
            self.parts.append(GlyphPart())
        self.parts[0].outer = value

    @property
    def hole_contours(self) -> List[List[Tuple[int, int]]]:
        """Holes of the first part (for backward compatibility)."""
        if self.parts:
            return self.parts[0].holes
        return []

    @hole_contours.setter
    def hole_contours(self, value: List[List[Tuple[int, int]]]) -> None:
        if not self.parts:
            self.parts.append(GlyphPart())
        self.parts[0].holes = value

    @property
    def status(self) -> str:
        return "ok" if self.conf >= 50 else "fix"

    @property
    def codepoint(self) -> str:
        if not self.char:
            return ""
        return format(ord(self.char), "x")


@dataclass
class Word:
    """An OCR-detected word containing one or more symbols."""

    index: int
    text: str                       # editable, length must stay constant
    original_text: str              # original tesseract text
    box: BBox                       # word bounding box in image coords
    conf: float                     # average word confidence
    symbols: List[Symbol] = field(default_factory=list)
    marked: bool = False              # user-toggled "green" state for export
    baseline_y: int = 0             # estimated baseline y (image top-down coords)
    committed: bool = False         # True once glyphs have been added to a project

    def set_text(self, new_text: str) -> bool:
        """Update the word text and propagate chars to symbols.

        Returns True if accepted (same length), False otherwise.

        Resets ``committed`` so the word is re-committed to the project DB on
        the next mark — the old glyphs (under the previous chars) are stale.
        """
        if len(new_text) != len(self.text):
            return False
        self.text = new_text
        for sym, ch in zip(self.symbols, new_text):
            sym.char = ch
        self.committed = False
        return True

    def compute_baseline(self) -> int:
        """Estimate the baseline y-coordinate from symbol bbox bottoms.

        Most letters sit on the baseline, so their bottom-y (bbox.y + bbox.h)
        equals the baseline. Descenders (у, р, ф, д, ц, щ) go below it.
        We take the minimum (highest) bottom-y, which is likely the baseline
        for non-descending letters. Using the 25th percentile is more robust
        against outliers.

        Returns the baseline y in image top-down coordinates.
        """
        import numpy as np
        if not self.symbols:
            return self.box[1] + self.box[3]  # fallback: bottom of word bbox
        bottoms = [sym.box[1] + sym.box[3] for sym in self.symbols]
        # The baseline is at the top of the bottom cluster — use the 25th
        # percentile (most letters are non-descenders).
        baseline = int(np.percentile(bottoms, 25))
        self.baseline_y = baseline
        return baseline


# ---------------------------------------------------------------------------
# Line-level baseline estimation
# ---------------------------------------------------------------------------

# Characters whose bbox bottom does NOT coincide with the baseline: they
# either float above it (quotes, apostrophes, diacritics) or are full-height
# brackets. Such characters must not anchor a per-word baseline estimate.
# Stored as the actual characters; comparison is per-symbol char.
_FLOATING_CHARS = set('"“”‘’´`ˆ¨˜˘˙˚¸˝„‟′″‴‵‶‷‹›«»')


def _is_baseline_anchoring(sym: Symbol) -> bool:
    """True if the symbol's bbox bottom is a reliable baseline anchor.

    Floating punctuation (quotes, apostrophes, diacritics) sits entirely
    above the baseline, so its bottom is NOT the baseline. Such symbols are
    excluded from the baseline estimate so that a word consisting only of
    punctuation (e.g. ``"``) does not collapse its baseline onto the glyph.
    """
    return sym.char not in _FLOATING_CHARS


def _word_has_anchor(word: Word) -> bool:
    """True if the word contains at least one baseline-anchoring symbol."""
    return any(_is_baseline_anchoring(s) for s in word.symbols)


def compute_line_baselines(words: List[Word]) -> None:
    """Assign each word a baseline.

    **Anchoring words** (containing letters/digits) get a per-word baseline
    computed from their own symbols' bbox bottoms (25th percentile). This
    guarantees the baseline is consistent with the word's own bbox — the
    key invariant for correct glyph positioning in word-local coordinates.

    **Floating words** (punctuation-only, e.g. ``"``) inherit the baseline
    from the nearest anchoring line **below** them — floating punctuation
    sits above the text it quotes. If no suitable line is found, they fall
    back to their own per-word estimate.

    This approach ensures that for every word, the stored ``baseline_y`` is
    consistent with the word's bbox: the baseline falls within the word's
    vertical extent (for anchoring words) or below it (for floating words),
    so the word-local translation at commit time always produces a valid
    in-range baseline.
    """
    if not words:
        return

    anchoring_words = [w for w in words if _word_has_anchor(w)]
    floating_words = [w for w in words if not _word_has_anchor(w)]

    # --- Pass 1: compute per-word baselines for anchoring words ---
    # Each anchoring word gets its own baseline from its own symbols.
    # This guarantees baseline-bbox consistency within each word.
    for w in anchoring_words:
        w.compute_baseline()

    # --- Pass 2: group anchoring words into lines (for floating words) ---
    # We need line grouping only to provide a baseline reference for
    # floating words. Group by vertical overlap with tight tolerance.
    anchoring_words.sort(key=lambda w: w.box[1])
    lines: List[List[Word]] = []
    for w in anchoring_words:
        wy, wh = w.box[1], w.box[3]
        w_top, w_bot = wy, wy + wh
        placed = False
        for line in lines:
            ref = line[0]
            ry, rh = ref.box[1], ref.box[3]
            r_top, r_bot = ry, ry + rh
            gap = max(0, max(r_top, w_top) - min(r_bot, w_bot))
            tol = 0.2 * max(wh, rh)
            if gap <= tol:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])

    # Compute a representative baseline per line (for floating words).
    # Use the median of the per-word baselines on the line.
    import numpy as np
    line_baselines: List[tuple] = []  # (top, bottom, baseline)
    for line in lines:
        bls = [w.baseline_y for w in line]
        bl = int(np.median(bls))
        tops = min(w.box[1] for w in line)
        bots = max(w.box[1] + w.box[3] for w in line)
        line_baselines.append((tops, bots, bl))

    # --- Pass 3: attach floating-only words to the nearest line below ---
    # Floating punctuation sits ABOVE the baseline, so its inherited line
    # baseline is expected to be BELOW its bbox — no clamping here.
    for w in floating_words:
        wy, wh = w.box[1], w.box[3]
        w_bot = wy + wh
        candidates = [lb for lb in line_baselines if lb[0] >= w_bot - 1]
        if candidates:
            top, bot, bl = min(candidates, key=lambda lb: lb[0] - w_bot)
            if (top - w_bot) <= (bot - top) * 1.5 + 4:
                w.baseline_y = bl
                continue
        # No suitable line: fall back to per-word estimation.
        w.compute_baseline()