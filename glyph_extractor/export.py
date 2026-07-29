"""CSV export with per-symbol rows and preview PNGs."""
from __future__ import annotations

import csv
import os
from typing import List

import cv2
import numpy as np

from .models import Symbol, Word


CSV_HEADER = [
    "id",
    "codepoint",
    "label",
    "suggested_label",
    "status",
    "preview_path",
    "contour_pts",
    "hole_contours",
    "bbox",
    "notes",
]


def _save_preview(image: np.ndarray, sym: Symbol, out_path: str) -> None:
    """Save the symbol crop as a PNG preview."""
    x, y, w, h = sym.box
    img_h, img_w = image.shape[:2]
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(img_w, x + w)
    y1 = min(img_h, y + h)
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        # write a 1x1 placeholder so the file exists
        crop = np.zeros((1, 1, 3), dtype=np.uint8)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, crop)


def export_csv(words: List[Word], image: np.ndarray, out_dir: str) -> str:
    """Export all symbols from all words to a CSV + preview PNGs.

    Creates `<out_dir>/previews/` and writes `<out_dir>/step1_review.csv`.
    Returns the path to the written CSV.
    """
    previews_dir = os.path.join(out_dir, "previews")
    os.makedirs(previews_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, "step1_review.csv")
    sym_id = 0
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for word in words:
            for sym in word.symbols:
                preview_name = f"g{sym_id:03d}.png"
                preview_abs = os.path.join(previews_dir, preview_name)
                _save_preview(image, sym, preview_abs)
                preview_rel = f"previews/{preview_name}"

                contour_str = ";".join(f"{x},{y}" for x, y in sym.contour_pts)
                # Serialize hole contours: each hole is "x,y;x,y;..." and
                # holes are separated by "|".
                hole_str = "|".join(
                    ";".join(f"{x},{y}" for x, y in hole) for hole in sym.hole_contours
                )
                bbox_str = ",".join(str(v) for v in sym.box)
                notes = f"conf={sym.conf:.1f}"

                writer.writerow(
                    [
                        sym_id,
                        sym.codepoint,
                        sym.char,
                        sym.suggested_char,
                        sym.status,
                        preview_rel,
                        contour_str,
                        hole_str,
                        bbox_str,
                        notes,
                    ]
                )
                sym_id += 1
    return csv_path