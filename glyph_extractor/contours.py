"""Contour extraction per symbol using cv2.findContours."""
from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np

from .models import BBox, GlyphPart, Symbol


def extract_parts_for_symbol(
    gray: np.ndarray, box: BBox, pad: int = 2
) -> List[GlyphPart]:
    """Extract all glyph parts (external contours + their holes) for a symbol.

    Crops the grayscale image around the box (with padding), thresholds it,
    runs cv2.findContours with RETR_TREE, and returns a list of GlyphPart
    objects — one per top-level (external) contour, each carrying its own
    hole contours (children).  All coordinates are in absolute image space.

    Returns an empty list if no contour found.
    """
    img_h, img_w = gray.shape[:2]
    x, y, w, h = box
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img_w, x + w + pad)
    y1 = min(img_h, y + h + pad)

    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return []

    # Threshold: text is dark on light background (invert so text is white).
    # Use Otsu on the inverted image.
    _, thresh = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, hierarchy = cv2.findContours(
        thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return []

    parts: List[GlyphPart] = []
    if hierarchy is not None:
        h = hierarchy[0]  # shape (N, 4): [next, prev, first_child, parent]
        for idx in range(len(contours)):
            # A top-level (external) contour has parent == -1.
            if h[idx][3] != -1:
                continue
            outer = _contour_to_pts(contours[idx], x0, y0)
            holes: List[List[Tuple[int, int]]] = []
            child_idx = h[idx][2]  # first_child
            while child_idx != -1:
                holes.append(_contour_to_pts(contours[child_idx], x0, y0))
                child_idx = h[child_idx][0]  # next sibling
            parts.append(GlyphPart(outer=outer, holes=holes))
    else:
        # No hierarchy — treat every contour as a standalone part.
        for c in contours:
            parts.append(GlyphPart(outer=_contour_to_pts(c, x0, y0)))

    return parts


def _contour_to_pts(
    contour: np.ndarray, offset_x: int, offset_y: int
) -> List[Tuple[int, int]]:
    """Convert an OpenCV contour array to a list of (x, y) tuples in image coords."""
    pts: List[Tuple[int, int]] = []
    for pt in contour.reshape(-1, 2):
        pts.append((int(pt[0]) + offset_x, int(pt[1]) + offset_y))
    return pts


def fill_contours_for_symbols(gray: np.ndarray, symbols: List[Symbol]) -> None:
    """Populate `parts` for each symbol in-place."""
    for sym in symbols:
        sym.parts = extract_parts_for_symbol(gray, sym.box)