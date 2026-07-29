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
    "parts",
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
    """Export symbols from marked (green) words to a CSV + preview PNGs.

    Only words whose ``marked`` flag is True are included in the export.
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
            if not word.marked:
                continue
            for sym in word.symbols:
                preview_name = f"g{sym_id:03d}.png"
                preview_abs = os.path.join(previews_dir, preview_name)
                _save_preview(image, sym, preview_abs)
                preview_rel = f"previews/{preview_name}"

                # Backward-compatible single-part fields (first part only).
                contour_str = ";".join(f"{x},{y}" for x, y in sym.contour_pts)
                hole_str = "|".join(
                    ";".join(f"{x},{y}" for x, y in hole) for hole in sym.hole_contours
                )

                # Full multi-part serialization:
                #   parts are separated by "||"
                #   within a part: outer contour "x,y;x,y;..." then "/" then
                #   holes separated by "|", each "x,y;x,y;..."
                part_strs = []
                for part in sym.parts:
                    outer_s = ";".join(f"{x},{y}" for x, y in part.outer)
                    holes_s = "|".join(
                        ";".join(f"{x},{y}" for x, y in hole) for hole in part.holes
                    )
                    part_strs.append(f"{outer_s}/{holes_s}")
                parts_str = "||".join(part_strs)

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
                        parts_str,
                        bbox_str,
                        notes,
                    ]
                )
                sym_id += 1
    return csv_path