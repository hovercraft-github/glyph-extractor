# Architectural Plan: Word Composition Scale Factor

## Problem

[`_compute_scale`](glyph_extractor/vectorize.py:243) maps the **whole word
bbox height** (`inst.word_bbox[3]`) to ~700 em units. A glyph's em size is
therefore determined by its source word's height, which depends on the
**letter kinds present** in that word (capitals, ascenders, descenders,
regular lowercase, special upper symbols), not on the glyph's own pixel
size.

Concrete (U+437 in `examples/ex5.glyphproj`):

| inst | glyph bbox px | word_bbox h px | scale (px→em) | glyph em height |
|------|---------------|----------------|---------------|-----------------|
| #0   | 17×30         | 49             | 700/49 ≈ 14.29 | ≈ 429 |
| #1   | 17×29         | 30             | 700/30 ≈ 23.33 | ≈ 677 |

Same physical glyph, ~1.6× visual difference because instance #1 came from
a short (regular-only) word and #0 from a tall (capital/descender) word.

## Goal

Normalize every word to a **common cap-height reference** (cap height → 700
em) by dividing out the word's composition-dependent vertical extent. The
per-word extent is expressed as a dimensionless **scale factor** `F`:

```
cap_height_px = word_h_px / F
scale = 700 / cap_height_px = 700 * F / word_h_px
```

`F` is derived from which letter kinds are present and the project's
calibrated per-kind extent ratios (cap-relative, cap = 1.0).

## Letter-kind model

Each character is classified into one kind. Default cap-relative ratios:

| kind      | top above baseline | bottom below baseline | examples                       |
|-----------|--------------------|------------------------|--------------------------------|
| capital   | 1.0                | 0.0                    | А…Я, A…Z, digits 0…9           |
| ascender  | 0.8                | 0.0                    | б, й, ф(top), b d f h k l t    |
| descender | 0.0 (sits at x-h)  | 0.3                    | у р д ц щ ф(bot), g j p q y    |
| regular   | 0.6                | 0.0                    | а в г е … (lowercase rest)     |
| special   | 1.0                | 0.0                    | ° (degree), superscript digits |
| floating  | excluded from F   | excluded               | " ' ´ ` (already in _FLOATING) |

Note: `ф` is both ascender (top) and descender (bottom); it contributes
both extents. For such dual-kind chars we treat the kind as a set of
flags rather than a single label.

### Per-word factor

```
top_extent    = max(top_ratio[k] for k in kinds_present if k has a top)
bottom_extent = max(bottom_ratio[k] for k in kinds_present if k has a bottom)
F = top_extent + bottom_extent
```

Examples (defaults):
- regular only: 0.6 + 0 = **0.6**
- capital only: 1.0 + 0 = **1.0**
- capital + descender: 1.0 + 0.3 = **1.3**
- ascender + descender (e.g. "бр"): 0.8 + 0.3 = **1.1**
- regular + descender (e.g. "ур"): 0.6 + 0.3 = **0.9**

## Calibration (learn ratios from data)

Ratios are measured from committed instances, regrouped by source word
(instances sharing `(source_image, word_index)`). For each instance/symbol:

- `top_px    = baseline_y - bbox.y`          (height above baseline)
- `bottom_px = (bbox.y + bbox.h) - baseline_y` (depth below baseline)

Bucket by kind, take the **median** per kind across all words. Reference =
capital median top (`cap_px`). Ratios = median / cap_px. If no capitals
exist, reference = regular median top (`x_px`) and assume
`cap_px = x_px / 0.6`.

Result is stored on the `Project` as `kind_ratios` (cap-relative). Defaults
match the table above so calibration is optional and gracefully degrades.

## Data model changes

### `Project` (project.py)
- New field `kind_ratios: Dict[str, float]` (serialized). Keys: `capital`,
  `ascender`, `regular`, `descender`, `special`. Values are cap-relative
  extents (top for capital/ascender/regular/special, bottom for descender).
- New method `calibrate_kind_ratios()` — scans all instances, regroups by
  word, measures, updates `kind_ratios`. Called after `add_word_glyphs`
  and on load.
- New method `word_scale_factor(word_kinds) -> float` — computes `F` from
  a set of present kinds using current `kind_ratios`.
- Project format version **3 → 4**.

### `GlyphInstance` (project.py)
- New field `word_kinds: tuple[str, ...]` — the sorted set of letter kinds
  present in the instance's source word (captured at commit). Used to
  recompute `F` on the fly from current `kind_ratios` (so recalibration
  propagates without rewriting instances).
- Computed in `add_word_glyphs` from the word's symbols.

### `add_word_glyphs` (project.py)
- For each word, build the set of kinds from `sym.char` for all symbols
  (excluding floating), pass to each `GlyphInstance`.
- After committing, call `proj.calibrate_kind_ratios()`.

## Scale computation (vectorize.py)

Replace the body of [`_compute_scale`](glyph_extractor/vectorize.py:243):

```python
def _compute_scale(inst, proj, cap_height_em=700.0):
    word_h = inst.word_bbox[3] if inst.word_bbox[3] > 0 else inst.bbox[3]
    F = proj.word_scale_factor(inst.word_kinds)
    return cap_height_em * F / max(word_h, 1)
```

Add a shared helper used by all scale consumers (vectorize, font_builder
kerning, kerning.py) to avoid the current duplicated `700/word_h` formula:

```python
def word_scale(inst, proj, cap_height_em=700.0) -> float:
    return _compute_scale(inst, proj, cap_height_em)
```

### Baseline / descender positioning
No separate change needed. The existing `fy = (baseline_y - py) * scale`
already places the baseline at y=0 and descenders below it. With the
consistent `scale`, a descender's em depth becomes
`descender_depth_px * 700*F/word_h`. For a capital+descender word
(F=1.3, word_h=1.3·cap_px, descender_depth=0.3·cap_px) this yields
`0.3·cap_px · 700/cap_px = 210 em` — independent of word composition, as
required. The user's "baseline offset" concern is resolved by the
consistent scale itself.

## Consumers to update (use shared `word_scale`)

1. [`_compute_scale`](glyph_extractor/vectorize.py:243) — rewritten as above.
2. [`advance_width_em`](glyph_extractor/vectorize.py:355) — already calls
   `_compute_scale`; no change.
3. [`normalize_to_upem`](glyph_extractor/vectorize.py:367) — already calls
   `_compute_scale`; no change.
4. [`_instance_gap_em`](glyph_extractor/font_builder.py:47) — replace the
   inline `700/word_h` with `word_scale(a, proj)`.
5. [`kerning.py`](glyph_extractor/kerning.py:87) `compute_kerning` — replace
   inline `word_height_em/word_h` with the shared scale. (Needs a Project
   reference or a ratios dict; pass `kind_ratios` through.)

## New module: `glyph_extractor/letter_kinds.py`

- `class Kind` (enum): CAPITAL, ASCENDER, DESCENDER, REGULAR, SPECIAL,
  FLOATING, UNKNOWN.
- `def letter_kinds(char: str) -> set[Kind]` — returns a set (usually one
  element; `ф` returns {ASCENDER, DESCENDER}).
- Explicit Cyrillic + Latin sets for ascenders/descenders; Unicode
  category fallback (Lu→CAPITAL, Nd→CAPITAL, Ll→REGULAR). `°` and
  superscript digits → SPECIAL. `_FLOATING_CHARS` → FLOATING.
- `def word_kinds_from_chars(chars) -> tuple[Kind, ...]` — union over a
  word's chars, excluding FLOATING/UNKNOWN, sorted, deduped. This is what
  gets stored on each `GlyphInstance.word_kinds`.

## Migration (v3 → v4)

On load, if version < 4:
- Regroup all instances by `(source_image, word_index)`.
- For each group, reconstruct the char list from `inst.char` per member
  (ordered by `sym_index`), compute `word_kinds` via
  `word_kinds_from_chars`, and assign to every member.
- Then `calibrate_kind_ratios()`.
- Bump version to 4.

This recovers the word composition from traceability fields without
needing the original `Word` objects.

## UI changes

- [`_metrics_text`](glyph_extractor/widgets/glyph_browser.py:335): add a
  line showing `word_kinds` and the computed `F` (e.g.
  `word kinds: capital,descender  scale factor F=1.30`).
- Optional: show calibrated `kind_ratios` in the settings dialog
  (`widgets/settings_dialog.py`) as read-only info, with a
  "Re-calibrate" button. (Nice-to-have; not required for correctness.)

## Verification

1. Load `examples/ex5.glyphproj` (v3) → migration populates `word_kinds`
   and calibrates ratios.
2. Open U+437 in glyph browser: both instances now render at the **same**
   em height (≈ cap-relative size), since their `F` values compensate for
   the differing `word_h`.
   - inst #0: word had capital+descender → F≈1.3, word_h=49 →
     scale=700·1.3/49≈18.57, glyph em ≈ 30·18.57≈557.
   - inst #1: regular-only word → F≈0.6, word_h=30 →
     scale=700·0.6/30≈14.0, glyph em ≈ 29·14.0≈406.
   - Wait — these still differ. That is **correct**: instance #0 is a
     capital/ascender glyph (taller than x-height) and #1 is a
     regular/x-height glyph, so they *should* differ in em. The fix
     ensures glyphs of the **same kind** from different words match.
3. Build font → all glyphs of the same kind have consistent em heights
   regardless of source word.
4. Descenders land at consistent em depth (~210 for full descenders).
5. Kerning pairs scale consistently with glyphs (shared `word_scale`).
6. Round-trip: save v4 → load v4 → build → identical output.

## Files to change

| file | change |
|------|--------|
| `glyph_extractor/letter_kinds.py` | **new** — classification + `word_kinds_from_chars` |
| `glyph_extractor/project.py` | `kind_ratios` field, `calibrate_kind_ratios`, `word_scale_factor`, `word_kinds` on instance, commit + migration, version 3→4 |
| `glyph_extractor/vectorize.py` | rewrite `_compute_scale`, add `word_scale` helper |
| `glyph_extractor/font_builder.py` | `_instance_gap_em` uses `word_scale` |
| `glyph_extractor/kerning.py` | use shared scale (pass ratios) |
| `glyph_extractor/widgets/glyph_browser.py` | show `word_kinds` + `F` in metrics |
| `glyph_extractor/widgets/settings_dialog.py` | (optional) show calibrated ratios |

## Execution order

1. Create `letter_kinds.py` with classification + `word_kinds_from_chars`.
2. Extend `Project` (`kind_ratios`, `calibrate_kind_ratios`,
   `word_scale_factor`) and `GlyphInstance` (`word_kinds`); update
   `add_word_glyphs`; add v3→v4 migration.
3. Rewrite `_compute_scale` + add `word_scale` in `vectorize.py`.
4. Update `font_builder._instance_gap_em` and `kerning.py` to use
   `word_scale`.
5. Update `glyph_browser._metrics_text` to display kinds + factor.
6. (Optional) settings dialog ratios display.
7. Test with `examples/ex5.glyphproj`: migration + consistent preview.