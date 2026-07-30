"""Contour extraction per symbol using cv2.findContours."""
from __future__ import annotations

import cv2
import numpy as np
from scipy.interpolate import splprep, splev
from scipy.ndimage import gaussian_filter1d

from .models import BBox, GlyphPart, Symbol


def extract_parts_for_symbol(
    gray: np.ndarray, box: BBox, pad: int = 2
) -> list[GlyphPart]:
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

    parts: list[GlyphPart] = []
    if hierarchy is not None:
        h = hierarchy[0]  # shape (N, 4): [next, prev, first_child, parent]
        for idx in range(len(contours)):
            # A top-level (external) contour has parent == -1.
            if h[idx][3] != -1:
                continue
            outer = _contour_to_pts(contours[idx], x0, y0)
            holes: list[list[tuple[int, int]]] = []
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


def approximate_contour(cnt: list[tuple[int, int]], num_points: int = 20) -> list[tuple[int, int]]:
    """Approximate a contour with a fixed number of points using spline interpolation.

    Args:
        cnt: list of (x, y) tuples representing the contour points.
        num_points: Desired number of points in the approximated contour.

    Returns:
        list of (x, y) tuples representing the approximated contour.
    """
    if len(cnt) < 5:
        return cnt  # Not enough points to approximate

    x, y = zip(*cnt)  # unzip into separate x and y coordinate lists

    # Create a closed loop by appending the first point at the end
    x = np.append(x, x[0])
    y = np.append(y, y[0])

    # Parameterize the points
    tck, u = splprep([x, y], s=0, per=True)

    # Generate new points along the spline
    u_new = np.linspace(0, 1, num_points)
    x_new, y_new = splev(u_new, tck)
    
    new_cnt = [(int(i), int(j)) for i, j in zip(x_new, y_new)]

    return new_cnt


def smooth_with_gaussian(points, sigma=1.0):
    """
    Smooth contours using Gaussian filter
    """
    if len(points) < 3:
        return points
    
    points = np.array(points)
    
    # Pad the points for cyclic smoothing
    padded = np.vstack([points[-3:], points, points[:3]])
    
    # Apply Gaussian filter
    smoothed = gaussian_filter1d(padded, sigma=sigma, axis=0, mode='wrap')
    
    # Remove padding
    smoothed = smoothed[3:-3]
    
    return smoothed.tolist()


def _contour_to_pts(
    src_contour: np.ndarray, offset_x: int, offset_y: int
) -> list[tuple[int, int]]:
    """Convert an OpenCV contour array to a list of (x, y) tuples in image coords."""
    contour = src_contour + np.array([[offset_x, offset_y]])
    perimeter = cv2.arcLength(contour, True)
    epsilon = 0.005 * perimeter
    contour = cv2.approxPolyDP(contour, epsilon, True)
    pts: list[tuple[int, int]] = [(int(pt[0]), int(pt[1])) for pt in contour.reshape(-1, 2)]
    # The following attempts do not give acceptable results:
    # pts = approximate_contour(pts, num_points=50)
    # pts = smooth_with_gaussian(pts, sigma=1.0)
    return pts


def fill_contours_for_symbols(gray: np.ndarray, symbols: list[Symbol]) -> None:
    """Populate `parts` for each symbol in-place."""
    for sym in symbols:
        sym.parts = extract_parts_for_symbol(gray, sym.box)