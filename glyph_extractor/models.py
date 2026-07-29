"""Data models for words and symbols."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


BBox = Tuple[int, int, int, int]  # (x, y, w, h) in image coordinates


@dataclass
class Symbol:
    """A single glyph within a word."""

    index: int
    char: str                       # current label (editable via word edit)
    suggested_char: str             # original tesseract character
    box: BBox                       # tesseract box in image coords (x, y, w, h)
    conf: float                     # tesseract confidence for this symbol
    contour_pts: List[Tuple[int, int]] = field(default_factory=list)
    preview_path: str = ""

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