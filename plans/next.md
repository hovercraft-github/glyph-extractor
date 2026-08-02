Понял. Мой подход через Tesseract+contours нежизнеспособен. Вы сделали собственный инструмент. Сейчас изучу его и пример вывода, потом подумаю про улучшения.

CSV имеет новые поля: `hole_contours`, `parts` — которых не было в моём формате. 15 глифов из одного документа. Сейчас клонирую репо и изучу:

Теперь прочитаю ключевые файлы — `ocr.py`, `contours.py`, `export.py`, `models.py`:

Now I have a complete understanding of the tool. Let me study the example CSV output to understand what's actually being captured:

Now I have a thorough understanding. Let me analyze the tool and think about the two improvement areas the user asked about.

Now I have a complete picture. Let me look at the actual CSV data more closely — particularly the `parts` field and `bbox` format — and check what information is already available for kerning and baseline:

Now I have a complete understanding. Let me synthesize my analysis.

# Analysis of glyph-extractor

## What the tool already does well

1. **Word-level OCR via `image_to_data`** — Tesseract finds words (not isolated contours), which eliminates the fundamental flaw of my approach (picking random contours from the image). Tesseract segments text into words first, then the user reviews.

2. **Per-symbol bounding boxes via `image_to_boxes`** — each glyph gets a real bbox from Tesseract's character segmentation, not from `findContours` on random shapes.

3. **Multi-part contours** — `GlyphPart` model with `outer` + `holes` (e.g. the two holes in «А», the dot + body of «i»). Uses `RETR_TREE` hierarchy correctly.

4. **User review workflow** — PyQt5 GUI: word list (left), symbol view with boxes (top-right), contour view (bottom-right). User edits word text (same length enforced), marks words green with Enter, deletes bad words with Delete. Export only marked words.

5. **CSV format** — `id, codepoint, label, suggested_label, status, preview_path, contour_pts, hole_contours, parts, bbox, notes`. The `parts` field is the full multi-part serialization (outer/holes separated by `||`).

## What's missing — the two areas you asked about

### 1. Kerning data for fontTools auto-kerning

**Problem:** The CSV exports per-symbol data but **loses word context**. There's no `word_id`, no word bounding box, no adjacent symbol information. Without knowing which symbols are adjacent in a word, we can't compute kerning pairs.

**What fontTools needs for auto-kerning:**
- For each pair of adjacent glyphs (A,B), the **kerning value** = how much to adjust the advance width of A when followed by B. This is typically `advance_width(A) + kerning(A,B) - actual_gap(A,B)`.
- The **actual gap** between adjacent symbols = `(x_left of B) - (x_right of A)` — measured from the bbox or contour.

**What's already in the CSV:**
- `bbox` = `x, y, w, h` per symbol → we have x positions.
- But without word grouping, we don't know which symbols are adjacent.

**Solution: add `word_id` and `word_bbox` and `symbol_index_in_word` to the CSV.**

### 2. Baseline leveling

**Problem:** Each symbol's bbox has a `y` coordinate (top), but the **baseline** (the invisible line on which letters sit) is not explicitly stored. Different symbols in a word can have different heights (e.g. «A» sits on the baseline, «у» descends below it, «(» may span above and below). Without knowing the baseline, we can't properly position glyphs in the font.

**What's already in the data:**
- All symbols in a word share approximately the same baseline.
- The `y + h` (bottom of bbox) of most symbols = baseline position.
- But descenders («у», «р», «д») go below the baseline, so their `y + h` > baseline.
- Ascenders («б», «ф») go above the x-height but still sit on the baseline.

**Solution: compute baseline from the word's symbol set and store it per word.**

## Proposed improvements

Now let me implement the actual code changes in the glyph-extractor repo:

Now update `export.py` to add the new columns and compute baseline:

Now update the row-writing logic in `export_csv` to include the new fields:

Now update `main_window.py` to compute baseline before export:

Now create the `kerning.py` post-processing utility that reads the CSV and computes kerning pairs:

Now let me test the kerning utility against the example CSV:

0 kerning pairs and 0 baselines — because the example CSV was produced **before** our improvements (it doesn't have `word_id` / `word_baseline` columns). Let me verify:

Correct — old format without our new columns. The code correctly handles this (returns 0 pairs). When you export a new CSV with the updated tool, the `word_id` and `word_baseline` columns will be populated and the kerning utility will work.

Now let me verify the changes compile cleanly:

Everything compiles and the baseline computation works correctly.

# Итог

## Что я изучил

Ваш `glyph-extractor` — **правильный подход**: Tesseract сначала находит **слова** (через `image_to_data`), потом сегментирует их на символы (через `image_to_boxes`), и только потом извлекает контуры. Пользователь ревизит слова в GUI, помечает правильные (Enter=зелёный), удаляет мусор (Delete). Экспорт — только помеченных. Это кардинально отличается от моего подхода, где я искал контуры по всему изображению.

## Что я изменил в glyph-extractor

### 1. Kerning — добавлены 5 новых колонок в CSV

| Колонка | Что хранит | Зачем |
|---|---|---|
| `word_id` | ID слова (из OCR) | Группировка соседних глифов |
| `word_text` | Текст слова (возможно отредактированный) | Верификация пар |
| `sym_idx` | Позиция символа в слове (0-based) | Порядок для пар |
| `word_bbox` | `x,y,w,h` слова в координатах изображения | Вычисление масштаба |
| `word_baseline` | Y-координата baseline (top-down) | Выравнивание глифов в TTF |

Изменения в файлах:
- `models.py` — `Word.baseline_y` + `Word.compute_baseline()` (25-й перцентиль нижних граней bbox)
- `export.py` — `CSV_HEADER` расширен, row-writer дополнен
- `main_window.py` — baseline вычисляется перед экспортом

### 2. Baseline leveling — `Word.compute_baseline()`

```python
def compute_baseline(self) -> int:
    bottoms = [sym.box[1] + sym.box[3] for sym in self.symbols]
    baseline = int(np.percentile(bottoms, 25))
    self.baseline_y = baseline
    return baseline
```

Принцип: большинство букв сидят на baseline, их `bbox.y + bbox.h` = baseline. Буквы с нижними выносами («у», «р», «д», «ф») уходят ниже. Берём 25-й перцентиль — это robust-оценка baseline, устойчивая к 1–2 descender'ам в слове.

### 3. Новый файл `glyph_extractor/kerning.py`

Постпроцессинг CSV для kerning:

```bash
python -m glyph_extractor.kerning <csv_path> --out-fea kerning.fea --out-json metrics.json
```

- Группирует строки по `word_id`
- Для каждой пары соседних символов `(A, B)` вычисляет gap: `(left_contour_B) - (right_contour_A)`, масштабирует в em-единицы
- Усредняет по всем словам с той же парой
- Выход: `.fea` файл для fontTools feaLib или JSON с парами + baselines

## Как это использовать в нашем пайплайне

```
glyph-extractor (GUI)
    │ пользователь открывает PNG, ревизит слова, помечает, экспорт
    ▼
step1_review.csv (с word_id, sym_idx, word_baseline)
    │
    ▼
python -m glyph_extractor.kerning step1_review.csv --out-fea kerning.fea
    │
    ▼
kerning.fea + baseline данные
    │
    ▼
assemble_ttf.py (новый, ещё не написан)
    │ читает CSV + kerning.fea
    │ позиционирует каждый глиф относительно baseline
    │ строит TTF с GPOS kern table
    ▼
handwriting.ttf (с кернингом + правильным baseline)
```

## Что НЕ сделано (на следующую итерацию)

1. **`assemble_ttf.py`** — скрипт, который читает улучшенный CSV и собирает TTF:
   - Мульти-частные контуры (из `parts` — outer + holes)
   - Baseline-позиционирование (y=0 в font coords = baseline)
   - Advance width из `bbox.w`
   - Kerning из `.fea` файла

2. **Интеграция PDF→PNG** — glyph-extractor сейчас работает только с PNG. Нужно добавить открытие PDF (через pdftoppm).

3. **Мульти-страничный режим** — сейчас один PNG за раз. Для нашего пайплайна нужно обрабатывать несколько сканов и собирать один шрифт.

Скажите, что дальше — реализовать `assemble_ttf.py`, или сначала протестировать glyph-extractor с новыми колонками на реальном скане?

---

# Обновление: проектная БД + сборка TTF (реализовано)

Вместо `assemble_ttf.py` реализован **проектный механизм** — внутренняя БД
глифов, из которой шрифт собирается напрямую. CSV-экспорт сохранён как
опциональный отладочный инструмент.

## Новые файлы

- `glyph_extractor/project.py` — `Project` + `GlyphInstance` датаклассы,
  JSON load/save, `add_word_glyphs`, авто-выбор лучшего инстанса
  (`quality_score` = OCR conf + бонус за дыры/части), `collection_summary`.
  Формат: один `*.glyphproj` JSON-файл.
- `glyph_extractor/vectorize.py` — `pypotrace` (модуль `potrace`) трассировка
  контуров в кубические кривые Безье; `to_tt_glyph` строит `TTGlyph` с
  позиционированием относительно baseline и масштабированием px→em.
- `glyph_extractor/font_builder.py` — `fonttools` `FontBuilder` сборка TTF
  из выбранных инстансов: head/name/hhea/OS2/cmap/glyf/hmtx + GPOS kern table
  (kerning считается из БД проекта по смежности `word_index`/`sym_index`).
- `glyph_extractor/widgets/vector_preview.py` — `QWidget` рендерит
  векторизованные кривые Безье с линией baseline.
- `glyph_extractor/widgets/glyph_browser.py` — браузер собранных кодпоинтов:
  список с бейджами количества, превью растра + вектора, метрики
  (bbox, advance, conf, baseline offset, parts/holes), кнопки
  "Set as best" / "Delete instance" / "Re-vectorize".

## Изменённые файлы

- `glyph_extractor/main_window.py` — меню **Project** (New/Open/Save/Save As/
  Build Font), статус-бар (имя проекта + счётчики), док-панель glyph browser
  справа. Пометка слова (Enter) теперь коммитит глифы в БД проекта
  (сигнал `word_marked`).
- `glyph_extractor/widgets/word_list.py` — добавлен сигнал `word_marked`,
  эмитится из `_toggle_mark`.

## Проверено

- Импорт всех модулей чистый.
- GUI конструируется (offscreen): меню File + Project, статус-бар.
- End-to-end: create project → add_word_glyphs (с превью PNG) → save JSON →
  reload → build_font → TTF с корректным cmap + GPOS.

## Что дальше (на следующую итерацию)

1. Протестировать на реальном скане: качество векторизации pypotrace,
   корректность baseline-позиционирования, читаемость шрифта.
2. PDF→PNG интеграция (pdftoppm).
3. Тюнинг `quality_score` / масштаба px→em по результатам.
4. Возможно: ручная правка контуров в браузере перед сборкой шрифта.
