"""Contour extraction per symbol using cv2.findContours."""
from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np

from .models import BBox, Symbol


def extract_contour_for_symbol(gray: np.ndarray, box: BBox, pad: int = 2) -> List[Tuple[int, int]]:
    """Extract the largest contour for the symbol region defined by `box`.

    Crops the grayscale image around the box (with padding), thresholds it,
    runs cv2.findContours, and returns the largest contour's points in
    absolute image coordinates. Returns an empty list if no contour found.
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

    contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    largest = max(contours, key=cv2.contourArea)
    pts: List[Tuple[int, int]] = []
    for pt in largest.reshape(-1, 2):
        pts.append((int(pt[0]) + x0, int(pt[1]) + y0))
    return pts


def fill_contours_for_symbols(gray: np.ndarray, symbols: List[Symbol]) -> None:
    """Populate `contour_pts` for each symbol in-place."""
    for sym in symbols:
        sym.contour_pts = extract_contour_for_symbol(gray, sym.box)