# Architectural Plan: Word-Local Coordinates

## Current regression (root cause found)

`_safe_baseline_y` in `vectorize.py` clamps the baseline to the glyph's bbox
`[top, bot]` for **all** glyphs whose baseline falls outside that range:

```python
if top <= inst.baseline_y <= bot:
    return inst.baseline_y
return bot  # clamps BOTH above-top AND below-bot
```

For a **floating glyph** like `"` (U+22), the line baseline is **below** the
quote's bbox (correct — the quote floats above the text line). So
`baseline_y > bot` → returns `bot` (the quote's own bottom) → the quote
collapses onto the baseline. This re-introduces the original U+22 bug AND
breaks every floating glyph (diacritics, apostrophes, etc.).

The clamp was added to fix stale baselines where `baseline_y < top` (baseline
above glyph top → glyph maps below the em box). But it incorrectly also clamps
the `baseline_y > bot` case (floating glyph), which is legitimate.

The `_safe_baseline` in `models.py` correctly distinguishes anchoring vs
floating words. But `_safe_baseline_y` in `vectorize.py` does not — it clamps
unconditionally. **This is the regression.**

## Two-phase plan

### Phase 1: Immediate fix (stop the bleeding)

Fix `_safe_baseline_y` to only clamp the dangerous case (baseline above glyph
top), not the floating case (baseline below glyph bottom):

```python
def _safe_baseline_y(inst: GlyphInstance) -> int:
    top = inst.bbox[1]
    bot = inst.bbox[1] + inst.bbox[3]
    if inst.baseline_y >= top:
        return inst.baseline_y  # within bbox OR below it (floating) — valid
    return bot  # baseline above glyph top — degenerate, clamp to bottom
```

- `baseline_y < top`: baseline above glyph → glyph maps below em box. CLAMP.
- `top <= baseline_y <= bot`: normal anchoring glyph. KEEP.
- `baseline_y > bot`: floating glyph, baseline below bbox. KEEP (correct).

This restores floating glyphs while still handling stale baselines.

### Phase 2: Word-local coordinate refactor (eliminate root cause)

Move the coordinate translation from vectorize-time (late, lossy, requiring
clamps) to commit-time (early, exact, single boundary).

#### Word-local coordinate convention

Origin: **word bbox top-left** (`Word.box` x, y).

| Field | Old (page-absolute) | New (word-local) |
|---|---|---|
| `bbox` | `(x, y, w, h)` page-absolute | `(x - word.x, y - word.y, w, h)` |
| `word_bbox` | `(x, y, w, h)` page-absolute | `(0, 0, word.w, word.h)` |
| `baseline_y` | page-absolute y | `word.baseline_y - word.y` |
| `parts[*].outer` | page-absolute pts | `(px - word.x, py - word.y)` |
| `parts[*].holes` | page-absolute pts | `(px - word.x, py - word.y)` |

Key property: `baseline_y` is always within `[0, word.h]` by construction
because the word's baseline (after line-grouping) is within the word's vertical
extent. This eliminates the entire class of off-box bugs — no clamp needed.

#### Why kerning still works

`_instance_gap_em` computes `min(b_lefts) - max(a_rights)` — a difference.
Subtracting the same `word.x` from both sides leaves the gap unchanged.
`word_bbox[3]` (height) is translation-invariant. No change needed.

#### Why display widgets are unaffected

`symbol_view.py`, `contour_view.py`, `word_list.py` operate on transient
`Word`/`Symbol` (page-absolute), not `GlyphInstance`. They already subtract
`word.box` x,y for local rendering. The refactor only touches the commit
boundary and downstream consumers.

#### Migration

Project format version **2 → 3**. On load, if version < 3: subtract
`word_bbox.x/y` from all coordinates (v2 `word_bbox` is page-absolute and
contains the exact offset). Then set `word_bbox = (0, 0, w, h)`.

#### Files to change

1. **`project.py` — `add_word_glyphs`**: translate to word-local at commit.
   Add `_copy_part_local(part, dx, dy)`. Bump version 2→3, add migration in
   `_instance_from_dict`/`_project_from_dict`. Update `GlyphInstance` docstring.
2. **`vectorize.py`**: delete `_safe_baseline_y` entirely. `_px_to_font` uses
   `inst.baseline_y` directly (no override param). `to_tt_glyph` and
   `normalize_to_upem` drop the clamp call.
3. **`models.py`**: delete `_safe_baseline` in `compute_line_baselines` —
   assign line baseline directly (word-local translation guarantees in-range).
   Keep line-grouping logic (still needed for floating words).
4. **`font_builder.py`**: no change (differences + heights are invariant).
5. **Display widgets / export**: no change (operate on transient objects).

#### Verification

1. Re-OCR test image → mark words → build font.
2. All glyphs within em box (no yMin/yMax outside `[descent, ascent]`).
3. U+22 floats above baseline (inherited from line).
4. Kerning pairs present in built font.
5. Font Viewer "Standard Preview", "Unicode Block: Cyrillic" populated.
6. Load old `ex5.glyphproj` (v2) → migration translates correctly.
7. Round-trip: save v3 → load v3 → build font → identical output.

## Summary

Phase 1 fixes the immediate regression (floating glyphs collapsed onto
baseline by an over-aggressive clamp). Phase 2 eliminates the root cause by
moving coordinate translation to the commit boundary, making all stored
geometry self-contained and removing the clamp hacks entirely.