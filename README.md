# Glyph Extractor

Glyph Extractor is a PyQt5 desktop application designed to facilitate the creation of glyph datasets from images. It allows users to open PNG images, review OCR-detected words and symbols, refine OCR errors, and export a detailed CSV dataset along with preview images for each extracted glyph.

## Features

- **OCR Integration**: Uses Tesseract to automatically detect words and per-symbol bounding boxes.
- **Interactive Review**: 
  - A three-panel interface for efficient data review.
  - Editable word list: Fix OCR text errors (maintaining character count).
  - Visual feedback: View symbol bounding boxes and extracted contours in real-time.
- **Contour Extraction**: Automatically extracts the largest contour for each glyph using OpenCV.
- **Dataset Export**: 
  - Exports a CSV file containing glyph IDs, codepoints, labels, status, bounding boxes, and contour points.
  - Saves individual preview PNGs for every extracted symbol.

## Tech Stack

- **Language**: Python 3.9+
- **GUI Framework**: PyQt5
- **Image Processing**: OpenCV (`cv2`), Pillow
- **OCR Engine**: Tesseract (via `pytesseract`)
- **Data Handling**: NumPy, CSV, Dataclasses

## Project Structure

```
glyph-extractor/
├── main.py                 # Entry point of the application
├── pyproject.toml          # Project configuration
├── requirements.txt        # Python dependencies
├── glyph_extractor/        # Core logic and GUI components
│   ├── app.py              # Application wiring
│   ├── main_window.py      # Main window layout and coordination
│   ├── ocr.py              # Tesseract OCR integration
│   ├── contours.py         # Glyph contour detection logic
│   ├── models.py           # Data models (Word, Symbol)
│   ├── export.py           # CSV and PNG export functionality
│   └── widgets/            # Custom PyQt5 widgets
│       ├── word_list.py    # Word list panel
│       ├── symbol_view.py  # Symbol bounding box viewer
│       └── contour_view.py # Glyph contour renderer
└── examples/               # Sample data and reference outputs
```

## Installation

### System Requirements

- **Tesseract OCR**: You must have the Tesseract binary installed on your system and available in your PATH.
  - Ubuntu: `sudo apt install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: Install via the official binaries.

### Python Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd glyph-extractor
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. **Run the application**:
   ```bash
   python main.py
   ```

2. **Open an Image**: Use `File > Open PNG` to load a source image. The app will automatically run OCR to detect words and symbols.

3. **Review and Edit**:
   - Select a word from the left panel to see its symbols and contours on the right.
   - Double-click a word in the list to edit its text (Note: character count must remain the same).
   - Delete incorrect words using the context menu or Delete key.

4. **Export Dataset**: Use `File > Export CSV` to save the processed glyphs and their corresponding preview images to a directory of your choice.

## Export Format

The exported CSV contains the following columns:
- `id`: Global symbol index.
- `codepoint`: Hexadecimal representation of the character.
- `label`: The current (possibly edited) character.
- `suggested_label`: The original OCR result.
- `status`: "ok" or "fix" based on confidence.
- `preview_path`: Relative path to the symbol's PNG preview.
- `contour_pts`: Semicolon-separated list of `x,y` coordinates for the glyph contour.
- `bbox`: Bounding box as `x,y,w,h`.
- `notes`: Additional info (e.g., OCR confidence).

## License

This project is licensed under the [GNU General Public License v3.0-or-later](LICENSE).

Note: This license was chosen to ensure compatibility with PyQt5, which is distributed under GPL v3.
