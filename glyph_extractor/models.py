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

    def set_text(self, new_text: str) -> bool:
        """Update the word text and propagate chars to symbols.

        Returns True if accepted (same length), False otherwise.
        """
        if len(new_text) != len(self.text):
            return False
        self.text = new_text
        for sym, ch in zip(self.symbols, new_text):
            sym.char = ch
        return True