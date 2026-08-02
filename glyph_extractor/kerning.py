"""Post-processing: compute kerning pairs and baseline data from CSV.

Reads a step1_review.csv (with the word_id / word_text / sym_idx /
word_baseline columns added by the improved export) and emits:

1. A kerning .fea file for fontTools feaLib, or a Python dict of
   {(glyphA, glyphB): kerning_value} for direct GPOS table construction.
2. A baseline summary per word.

The kerning value is computed from the gap between adjacent glyphs
within the same word, measured from contour edges (not bbox edges)
when available, falling back to bbox edges.

Usage:
    python -m glyph_extractor.kerning <csv_path> [--out-fea path] [--out-json path]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


def parse_csv(csv_path: Path) -> List[dict]:
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def group_by_word(rows: List[dict]) -> Dict[int, List[dict]]:
    """Group CSV rows by word_id, sorted by sym_idx."""
    words: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        wid = int(r.get("word_id", -1))
        if wid < 0:
            continue
        words[wid].append(r)
    for wid in words:
        words[wid].sort(key=lambda r: int(r.get("sym_idx", 0)))
    return words


def parse_bbox(s: str) -> Tuple[int, int, int, int]:
    """Parse 'x,y,w,h' or 'x,y,x1,y1' — we use x,y,w,h here."""
    parts = [int(v) for v in s.split(",")]
    return tuple(parts)  # type: ignore


def parse_contour_pts(s: str) -> List[Tuple[int, int]]:
    if not s:
        return []
    out = []
    for tok in s.split(";"):
        try:
            x, y = tok.split(",")
            out.append((int(x), int(y)))
        except ValueError:
            continue
    return out


def compute_gap(a: dict, b: dict) -> int:
    """Compute the pixel gap between adjacent symbols a (left) and b (right).

    Uses contour edges when available, falls back to bbox edges.
    """
    # Try contour-based gap (more precise).
    pts_a = parse_contour_pts(a.get("contour_pts", ""))
    pts_b = parse_contour_pts(b.get("contour_pts", ""))
    if pts_a and pts_b:
        right_a = max(p[0] for p in pts_a)
        left_b = min(p[0] for p in pts_b)
        return left_b - right_a
    # Fallback: bbox-based gap.
    bbox_a = parse_bbox(a["bbox"])
    bbox_b = parse_bbox(b["bbox"])
    right_a = bbox_a[0] + bbox_a[2]
    left_b = bbox_b[0]
    return left_b - right_a


def compute_kerning_pairs(
    rows: List[dict],
    word_height_em: float = 700.0,
    min_samples: int = 2,
) -> Dict[Tuple[str, str], float]:
    """Compute average kerning pairs from adjacent symbols in words.

    Returns {(glyphA, glyphB): kerning_value_in_em_units}.
    """
    words = group_by_word(rows)
    pair_gaps: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    pair_scales: Dict[Tuple[str, str], List[float]] = defaultdict(list)

    for wid, syms in words.items():
        if len(syms) < 2:
            continue
        # Compute the scale factor from word height to em.
        word_bbox = parse_bbox(syms[0].get("word_bbox", syms[0]["bbox"]))
        word_h = word_bbox[3] if len(word_bbox) >= 4 else 1
        scale = word_height_em / max(word_h, 1)

        for i in range(len(syms) - 1):
            a = syms[i]
            b = syms[i + 1]
            label_a = a["label"]
            label_b = b["label"]
            if not label_a or not label_b:
                continue
            gap_px = compute_gap(a, b)
            gap_em = gap_px * scale
            pair_gaps[(label_a, label_b)].append(gap_em)

    # Average gaps, keep only pairs with enough samples.
    kerning: Dict[Tuple[str, str], float] = {}
    for pair, gaps in pair_gaps.items():
        if len(gaps) >= min_samples:
            kerning[pair] = sum(gaps) / len(gaps)
        elif len(gaps) >= 1:
            # Keep single-sample pairs too, but they're less reliable.
            kerning[pair] = gaps[0]
    return kerning


def write_fea(kerning: Dict[Tuple[str, str], float], out_path: Path) -> None:
    """Write a fontTools feaLib-compatible .fea file."""
    lines = ["feature kern {"]
    for (a, b), value in sorted(kerning.items()):
        # Round to int — fontTools kerning is in integer font units.
        v = int(round(value))
        if v != 0:
            lines.append(f"    pos {a} {b} {v};")
    lines.append("} kern;")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(kerning: Dict[Tuple[str, str], float], baselines: dict, out_path: Path) -> None:
    """Write kerning + baseline data as JSON."""
    data = {
        "kerning_pairs": {f"{a}|{b}": v for (a, b), v in kerning.items()},
        "baselines": baselines,
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_baselines(rows: List[dict]) -> dict:
    """Extract per-word baseline data from the CSV."""
    words = group_by_word(rows)
    baselines = {}
    for wid, syms in words.items():
        bl = syms[0].get("word_baseline", "")
        wtext = syms[0].get("word_text", "")
        if bl:
            baselines[wid] = {"text": wtext, "baseline_y": int(bl)}
    return baselines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_path", type=Path)
    p.add_argument("--out-fea", type=Path, default=None, help="Output .fea file path")
    p.add_argument("--out-json", type=Path, default=None, help="Output .json file path")
    p.add_argument("--min-samples", type=int, default=2,
                   help="Minimum sample count to keep a kerning pair")
    args = p.parse_args(argv)

    rows = parse_csv(args.csv_path)
    print(f"Loaded {len(rows)} rows from {args.csv_path}")

    kerning = compute_kerning_pairs(rows, min_samples=args.min_samples)
    print(f"Computed {len(kerning)} kerning pairs")
    for pair, v in sorted(kerning.items()):
        print(f"  {pair[0]}|{pair[1]}: {v:.1f} em")

    baselines = compute_baselines(rows)
    print(f"Baselines for {len(baselines)} words")

    if args.out_fea:
        write_fea(kerning, args.out_fea)
        print(f"Wrote .fea: {args.out_fea}")
    if args.out_json:
        write_json(kerning, baselines, args.out_json)
        print(f"Wrote .json: {args.out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())