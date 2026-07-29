"""Contour extraction per symbol using cv2.findContours."""
from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np

from .models import BBox, Symbol


def extract_contour_for_symbol(
    gray: np.ndarray, box: BBox, pad: int = 2
) -> Tuple[List[Tuple[int, int]], List[List[Tuple[int, int]]]]:
    """Extract the outer contour and any hole contours for the symbol region.

    Crops the grayscale image around the box (with padding), thresholds it,
    runs cv2.findContours with RETR_TREE, and returns:
      - the largest (outer) contour's points in absolute image coordinates
      - a list of hole contours (children of the outer contour) in absolute coords

    Returns ([], []) if no contour found.
    """
    img_h, img_w = gray.shape[:2]
    x, y, w, h = box
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img_w, x + w + pad)
    y1 = min(img_h, y + h + pad)

    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return [], []

    # Threshold: text is dark on light background (invert so text is white).
    # Use Otsu on the inverted image.
    _, thresh = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, hierarchy = cv2.findContours(
        thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return [], []

    # Find the largest contour — this is the outer glyph boundary.
    largest_idx = max(range(len(contours)), key=lambda i: cv2.contourArea(contours[i]))
    largest = contours[largest_idx]

    outer_pts: List[Tuple[int, int]] = []
    for pt in largest.reshape(-1, 2):
        outer_pts.append((int(pt[0]) + x0, int(pt[1]) + y0))

    # Collect hole contours: direct children of the largest contour in the
    # hierarchy tree.  hierarchy shape is (1, N, 4) with
    # [next, prev, first_child, parent].
    hole_contours: List[List[Tuple[int, int]]] = []
    if hierarchy is not None:
        h = hierarchy[0]  # shape (N, 4)
        # first_child of the largest contour
        child_idx = h[largest_idx][2]
        while child_idx != -1:
            child = contours[child_idx]
            pts: List[Tuple[int, int]] = []
            for pt in child.reshape(-1, 2):
                pts.append((int(pt[0]) + x0, int(pt[1]) + y0))
            hole_contours.append(pts)
            # move to next sibling
            child_idx = h[child_idx][0]

    return outer_pts, hole_contours


def fill_contours_for_symbols(gray: np.ndarray, symbols: List[Symbol]) -> None:
    """Populate `contour_pts` and `hole_contours` for each symbol in-place."""
    for sym in symbols:
        outer, holes = extract_contour_for_symbol(gray, sym.box)
        sym.contour_pts = outer
        sym.hole_contours = holes