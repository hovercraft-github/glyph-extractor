"""OCR extraction using pytesseract."""
from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np
import pytesseract

from .models import BBox, Symbol, Word


def _box_from_data(data: dict, i: int, img_h: int) -> BBox:
    """Build an image-coordinate bbox (x, y, w, h) from image_to_data entry.

    Tesseract returns top-down y already, so no flip needed for image_to_data.
    """
    x = int(data["left"][i])
    y = int(data["top"][i])
    w = int(data["width"][i])
    h = int(data["height"][i])
    return (x, y, w, h)


def _parse_char_boxes(image: np.ndarray) -> List[Tuple[str, int, int, int, int, float]]:
    """Return list of (char, x, y, w, h, conf) from image_to_boxes.

    Tesseract box format: "char left bottom right top page"
    where bottom/top are measured from the BOTTOM of the image.
    We convert to image-top-down coordinates.
    """
    img_h = image.shape[0]
    boxes = pytesseract.image_to_boxes(image)
    results = []
    for line in boxes.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        ch = parts[0]
        left = int(parts[1])
        bottom = int(parts[2])
        right = int(parts[3])
        top = int(parts[4])
        # convert bottom-origin to top-origin
        y_top = img_h - top
        y_bottom = img_h - bottom
        x = left
        y = y_top
        w = right - left
        h = y_bottom - y_top
        results.append((ch, x, y, w, h, 0.0))
    return results


def extract_words(image: np.ndarray) -> List[Word]:
    """Run OCR and return a list of Word objects with grouped symbols.

    Uses image_to_data for word-level info (text, conf, bbox) and
    image_to_boxes for per-character boxes, then groups chars into words
    by spatial overlap with word bboxes.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    # Word-level data
    data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
    char_entries = _parse_char_boxes(gray)

    words: List[Word] = []
    word_idx = 0
    for i in range(len(data["text"])):
        txt = data["text"][i]
        if not txt or not txt.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        wbox = _box_from_data(data, i, image.shape[0])
        wx, wy, ww, wh = wbox

        # Collect char boxes that fall inside this word bbox.
        # Allow a small tolerance so chars slightly outside still match.
        tol = max(2, wh // 4)
        matched = []
        remaining = []
        for entry in char_entries:
            ch, cx, cy, cw, chh, _ = entry
            ccenter_x = cx + cw / 2
            ccenter_y = cy + chh / 2
            if (wx - tol) <= ccenter_x <= (wx + ww + tol) and (
                wy - tol
            ) <= ccenter_y <= (wy + wh + tol):
                matched.append(entry)
            else:
                remaining.append(entry)
        char_entries = remaining

        # Sort matched chars left-to-right.
        matched.sort(key=lambda e: e[1])

        # Build symbols. If char count != len(txt), fall back to using word
        # text characters with the word bbox divided evenly.
        symbols: List[Symbol] = []
        if matched and len(matched) == len(txt):
            for j, (ch, cx, cy, cw, chh, _) in enumerate(matched):
                symbols.append(
                    Symbol(
                        index=j,
                        char=txt[j],
                        suggested_char=ch,
                        box=(cx, cy, cw, chh),
                        conf=conf,
                    )
                )
        else:
            # Fallback: divide word bbox evenly across characters.
            n = len(txt)
            if n == 0:
                continue
            char_w = ww / n
            for j, ch in enumerate(txt):
                sx = int(wx + j * char_w)
                sw = int(char_w)
                symbols.append(
                    Symbol(
                        index=j,
                        char=ch,
                        suggested_char=ch,
                        box=(sx, wy, sw, wh),
                        conf=conf,
                    )
                )

        words.append(
            Word(
                index=word_idx,
                text=txt,
                original_text=txt,
                box=wbox,
                conf=conf,
                symbols=symbols,
            )
        )
        word_idx += 1

    return words