# Glyph Extractor

Glyph Extractor is a PyQt5 desktop application for turning scanned handwriting samples into a ready-to-use TrueType font. It runs Tesseract OCR on a source PNG, lets you review and correct the detected words, collects the corrected glyphs into a persistent **project database**, and assembles a `.ttf` with vectorized outlines, per-kind vertical normalization, and GPOS kerning — all from a single GUI session.

## Features

### OCR & review
- **OCR integration**: Tesseract (via `pytesseract`) detects words and per-symbol bounding boxes. Recognition language(s) and the tesseract binary path are configurable per project.
- **Interactive three-panel review**:
  - Left: editable word list. Double-click (or start typing) to fix OCR errors; the character count must stay the same.
  - Center-top: the cropped word image with red per-symbol bounding boxes.
  - Center-bottom: current word symbol bitmaps, each in its own color.
- **Synchronized zoom**: Ctrl + scroll-wheel zooms the symbol and contour views together.
- **Marking for collection**: press `Enter` on a word to toggle its "green" (collected) state. Editing a word auto-marks it. Only marked words are committed to the project DB and exported to CSV.
- **Committability guard**: words with no non-descender letter (e.g. descender-only or punctuation-only) cannot anchor a baseline and are shown faded; they become markable once a regular/capital/ascender letter is edited in.

### Contour extraction
- **Multi-part contours**: each symbol is decomposed into connected components (`GlyphPart`s), each with an outer contour and zero or more hole contours (e.g. the loop of `а`, `о`), via `cv2.findContours` with `RETR_TREE`.
- **Bbox-masked tracing**: the padded crop border is masked out so ink bleeding in from neighbouring glyphs is excluded — only ink within the symbol's own bbox is traced.

### Project database (`.glyphproj`)
- A project is a single JSON file that accumulates glyph instances across many source images. Each codepoint maps to a list of `GlyphInstance` records (one per occurrence in a marked word).
- **Word-local coordinates**: all geometry is translated to word-local pixel coordinates (origin = word bbox top-left) at commit time, so `baseline_y` is always within `[0, word.h]` by construction — no downstream clamping is needed.
- **Traceability**: every instance records its source image, word index, and symbol index, so adjacency can be reconstructed for kerning without the original `Word` objects.
- **Inline previews**: a small base64 PNG crop is stored with each instance for quick browsing.
- **Auto-selection**: the best instance per codepoint is auto-selected by a quality score (OCR confidence + hole-capture bonus + contour resolution, with a penalty for degenerate boxes). The user can override this in the glyph browser.
- **Schema versioning & migration**: the on-disk format is versioned (currently v5). Older projects are migrated on load (page-absolute → word-local coordinates, `word_kinds` reconstruction, ratio calibration).

### Glyph browser
- Far-right panel for reviewing collected glyphs:
  - Left: list of collected codepoints with character, hex codepoint, and instance-count badge; the selected ("best") instance is starred.
  - Right: per-instance list with raster preview icons, a **vectorized outline preview** rendered inside an em box (with a green baseline and per-kind reference lines drawn in distinct colors), and a metrics summary (bbox, advance width, OCR confidence, baseline offset, parts/holes/contour-point counts, word kinds, scale factor, source traceability).
  - Controls: **Set as best**, **Delete instance**, **Re-vectorize** (restores the original baseline and re-traces), **Put on baseline** (forces the baseline to the bbox bottom; correct for non-descenders, incorrect for descenders).

### Letter-kind classification & vertical normalization
- Each character is classified into vertical-extent kinds: `CAPITAL`, `ASCENDER`, `DESCENDER`, `REGULAR`, `SPECIAL`, `FLOATING`, `UNKNOWN` (a few glyphs span two kinds, e.g. Cyrillic `ф` is ascender + descender; capital+ascender letters like `Ё`, `Й` get a synthetic `capital_ascender` key).
- **Per-project ascender/descender lists** (editable in Settings; defaults suit Cyrillic + Latin handwriting) drive classification, baseline anchoring, the word scale factor, per-kind normalization, and kind-ratio calibration.
- **Per-word scale factor `F`**: expresses a word's vertical extent as a multiple of the cap height, so glyphs from words of different letter composition normalize to a common cap-height reference.
- **Per-kind normalization**: each glyph's own top is mapped to its kind's reference line in em units (capitals → cap height, ascenders → ascender line, regulars/descenders → x-height, capital+ascender → above the capitals line). Non-descender letters are automatically anchored to their bbox bottom ("Put on baseline") to correct per-word baseline estimation error.
- **Calibrated kind ratios**: per-kind extent ratios are re-estimated from committed instances (median per kind, cap-relative) whenever the instance set changes, so normalization reflects the actual handwriting rather than fixed defaults.

### Baseline estimation
- **Line-aware baselines**: anchoring words (containing letters/digits) get a per-word baseline from their own symbols' bbox bottoms (25th percentile). Floating words (punctuation-only, e.g. `"`) inherit the baseline from the nearest anchoring line **below** them, so floating punctuation sits correctly above the text it quotes.

### Vectorization & font building
- **Potrace vectorization**: glyph contour parts are re-rasterized and traced with `pypotrace` into cubic bezier segments (cached per instance), then converted to TrueType quadratic curves via `Cu2QuPen`.
- **TrueType font assembly** (`Project > Build Font…`): builds a minimal but valid `.ttf` with `head`/`name`/`hhea`/`os2`/`cmap`/`glyf`/`hmtx` tables using `fontTools.fontBuilder`, plus an optional **GPOS kern** table.
- **Kerning from the project DB**: average per-pair kerning values (in em units) are computed from the contour-edge gaps between strictly adjacent instances within the same source word, using the shared composition-normalized scale. A standalone CSV-driven kerning utility is also available (`python -m glyph_extractor.kerning`).
- **Configurable font metrics**: units-per-em, ascent, and descent are editable per project (Settings).

### Settings dialog (`Project > Settings…`, `Ctrl+,`)
- **OCR**: recognition languages (quick-pick checkboxes for common codes + free text, `+`-separated) and an optional tesseract executable path.
- **Letter classification**: editable ascender and descender character lists.
- **Font metrics**: units-per-em, ascent, descent (em units). Apply persists immediately if the project has a path and re-calibrates kind ratios.

### CSV export (fallback)
- `File > Export CSV…` exports marked words to `step1_review.csv` plus per-symbol preview PNGs. The CSV includes glyph IDs, codepoints, labels, status, bounding boxes, single-part and full multi-part contour serialization, word traceability fields (`word_id`, `word_text`, `sym_idx`, `word_bbox`, `word_baseline`), and OCR confidence notes.

## Tech Stack

- **Language**: Python 3.13+
- **GUI Framework**: PyQt5
- **Image Processing**: OpenCV (`opencv-python-headless`), Pillow
- **OCR Engine**: Tesseract (via `pytesseract`)
- **Vectorization**: `pypotrace` (cubic bezier tracing)
- **Font Building**: `fontTools` (FontBuilder, TTGlyphPen, Cu2QuPen, GPOS)
- **Data Handling**: NumPy, SciPy, dataclasses, JSON

## Project Structure

```
glyph-extractor/
├── main.py                          # Entry point
├── pyproject.toml                   # Project configuration + dependencies
├── requirements.txt                 # Pip dependencies
├── glyph_extractor/
│   ├── app.py                       # Application bootstrap
│   ├── main_window.py               # Main window layout, menus, project/word coordination
│   ├── ocr.py                       # Tesseract OCR integration
│   ├── contours.py                  # Multi-part contour (outer + holes) extraction
│   ├── models.py                    # Word/Symbol/GlyphPart models + line baseline estimation
│   ├── letter_kinds.py              # Letter-kind classification + word scale factor
│   ├── project.py                   # Project DB (GlyphInstance, serialization, migration)
│   ├── vectorize.py                 # Potrace tracing + px→em conversion + TTGlyph building
│   ├── font_builder.py              # TTF assembly + GPOS kerning from the project DB
│   ├── kerning.py                   # Standalone CSV-driven kerning utility
│   ├── export.py                    # CSV + preview PNG export
│   └── widgets/
│       ├── word_list.py             # Editable/markable word list
│       ├── symbol_view.py           # Word image fragment + bounding boxes
│       ├── contour_view.py          # Filled contour renderer (outer + holes)
│       ├── glyph_browser.py         # Collected-glyph browser + instance controls
│       ├── vector_preview.py        # Em-box vectorized outline preview + reference lines
│       └── settings_dialog.py       # Project settings (OCR, classification, metrics)
├── plans/                           # Design notes and planning documents
└── examples/                        # Sample data and reference outputs
```

## Installation

### System Requirements

- **Tesseract OCR**: install the binary and ensure it is on your PATH.
  - Ubuntu: `sudo apt install tesseract-ocr` (plus language packs, e.g. `tesseract-ocr-rus`)
  - macOS: `brew install tesseract`
  - Windows: install via the official binaries.
- **potrace** (required by `pypotrace`): install the `potrace` library/headers.
  - Ubuntu: `sudo apt install libpotrace-dev`
  - macOS: `brew install potrace`

### Python Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd glyph-extractor
   ```

2. (Recommended) create and activate a virtual environment, then install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   Or, with `uv`:
   ```bash
   uv sync
   ```

## Usage

1. **Run the application**:
   ```bash
   python main.py
   ```

2. **Create / open a project** (`Project` menu): a `.glyphproj` file stores collected glyphs and settings across sessions.

3. **Open an image** (`File > Open PNG…`, `Ctrl+O`): OCR runs automatically; words and per-symbol contours are populated for review.

4. **Review and edit**:
   - Select a word to inspect its symbols and contours.
   - Double-click (or start typing) to edit a word's text (length must stay the same).
   - Press `Enter` to toggle the green "marked" state; marked words are committed to the project DB.
   - Delete incorrect words with the context menu or the `Delete` key.

5. **Browse collected glyphs** (far-right panel): inspect vectorized previews, set the best instance per codepoint, delete bad instances, re-vectorize, or put a glyph on the baseline.

6. **Tune settings** (`Project > Settings…`, `Ctrl+,`): OCR languages, ascender/descender lists, and font metrics.

7. **Build a font** (`Project > Build Font…`): writes a `.ttf` from the project's selected glyph instances, with GPOS kerning.

8. **Export CSV** (`File > Export CSV…`, `Ctrl+E`): fallback export of marked words to a CSV + preview PNGs.

## Export Format

The exported CSV (`step1_review.csv`) contains the following columns:

- `id`: global symbol index.
- `codepoint`: hexadecimal codepoint of the character.
- `label`: the current (possibly edited) character.
- `suggested_label`: the original OCR character.
- `status`: `"ok"` or `"fix"` based on confidence.
- `preview_path`: relative path to the symbol's PNG preview.
- `contour_pts`: first-part outer contour, `x,y` pairs separated by `;`.
- `hole_contours`: first-part holes, each `x,y;x,y;…`, separated by `|`.
- `parts`: full multi-part serialization — parts separated by `||`, each `outer / holes` with holes separated by `|`.
- `bbox`: symbol bounding box as `x,y,w,h`.
- `notes`: additional info (e.g. `conf=<value>`).
- `word_id`, `word_text`, `sym_idx`: traceability back to the source word.
- `word_bbox`: word bounding box as `x,y,w,h`.
- `word_baseline`: estimated baseline y (image top-down coordinates).

## License

This project is licensed under the [GNU General Public License v3.0-or-later](LICENSE).

Note: this license was chosen to ensure compatibility with PyQt5, which is distributed under GPL v3.
