# J PDF Maker

Five Python/PyQt6 tools for making PDFs look the way you want:

- **Long_PDF_Maker.py** — stitch a multi-page PDF into a single long-page PDF while preserving vector quality, with draggable header/footer guides and a live preview.
- **Long_PDF_To_A4.py** — reverse the process by splitting long PDF pages into standard A4 portrait pages while preserving vector quality.
- **Merge_Two_Pages.py** — combine two PDF pages (e.g. the front and back of a document) onto a single page, with drag-to-position, edge/corner cropping, and undo/redo.
- **Redact_PDF.py** — scroll through a PDF, draw boxes over anything secret, and save a copy with that content permanently removed, with a live preview of the redacted result.
- **Markdown_to_PDF.py** — convert Markdown to a styled PDF (colored code blocks, wrapped lines, configurable page size / orientation / text scale) with a live rendered preview.

All apps share the same dark Catppuccin-style UI: a live preview and a row of controls that reflow to new rows when the window is narrow.

## Install

Requires Python 3.10+. Create a virtual environment and install the dependencies:

#### Mac:
```bash
python3 -m venv .venv
.venv/bin/pip install PyQt6 PyMuPDF markdown pygments
```

#### Windows:

```
py -m venv .venv
.venv\Scripts\pip install PyQt6 PyMuPDF markdown pygments
```

`Markdown_to_PDF.py` additionally needs a Chromium-based browser installed (Google Chrome, Chromium, Brave, or Edge) — it's used headlessly to render HTML → PDF. The app auto-detects the standard `/Applications/*.app` locations on macOS and anything on `PATH` elsewhere.

> Note: a separate PyPI package named `fitz` exists and is unrelated. You only need `PyMuPDF`, which provides the real `fitz` module. If you installed the wrong one, run `pip uninstall -y fitz frontend` first.

## Long_PDF_Maker.py — long-page stitcher

Stitches a multi-page PDF into one tall page, keeping everything vector.

```bash
.venv/bin/python Long_PDF_Maker.py
```

### Features

- Drag and drop a PDF (or click **Open PDF**, or press `Ctrl+O`)
- Auto-detects likely header and footer heights from the first page's text layout
- Draggable red (header) and green (footer) guide lines on the source preview with a thick, easy-to-grab hit band and round handles
- Spin-box fine-tuning with large `−` / `+` steppers; PNG DPI selector in 100-step increments
- Live, HiDPI-sharp preview of the stitched long-page output while you adjust
- **Keep header on page 1** toggle — if enabled, page 1 keeps its header while all other pages still have it removed
- Save as a vector PDF (`Ctrl+S`) or as a PNG at the chosen DPI (`Ctrl+E`)

### How it works

Each page is cropped by the chosen top/bottom point values, then every cropped page is placed onto one tall page via PyMuPDF's `show_pdf_page` — vectors are preserved. With **Keep header on page 1** on, the first page skips the top crop.

Default output names from the save dialogs:

- `<name>_long_page_vector.pdf`
- `<name>_long_page_vector.png`

### Shortcuts

| Action | Shortcut |
| --- | --- |
| Open PDF | `Ctrl+O` |
| Save PDF | `Ctrl+S` |
| Save PNG | `Ctrl+E` |

## Long_PDF_To_A4.py — long PDF to A4 pages

Splits each tall source page from top to bottom, fits its width to A4 portrait,
and keeps text and artwork as vector PDF content.

```bash
# GUI
.venv/bin/python Long_PDF_To_A4.py

# Command line
.venv/bin/python Long_PDF_To_A4.py input.pdf
.venv/bin/python Long_PDF_To_A4.py input.pdf -o output.pdf --margin-mm 5 --overlap-mm 10
```

### Features

- Drag and drop a long PDF, or open one with `Ctrl+O`
- Live preview of the resulting A4 pages
- Optional blank margin around each page
- Optional overlap that repeats content between adjacent pages
- Handles every source page, not only the first one
- Saves exact 210 × 297 mm A4 pages with vector quality (`Ctrl+S`)
- Command-line mode for direct or batch-style conversion

The default output name is `<name>_A4_pages.pdf`. The final page is not
stretched; unused space at its bottom remains blank.

## Merge_Two_Pages.py — two pages onto one

Combines two pages of a PDF onto a single page — handy for putting the front
and back of a document side by side (or one placed onto the other).

```bash
.venv/bin/python Merge_Two_Pages.py
```

### Features

- Drag and drop a PDF with 2+ pages (or click **Open PDF**, or press `Ctrl+O`)
- Pick which page is the **base** (stays fixed, fills the output) and which is
  the **moved** page (placed on top)
- **Pan / Crop mode toggle** (default is Crop):
  - *Crop mode* — drag the moved page's **corners** to crop in two directions,
    or drag an **edge** to crop in a single direction
  - *Pan mode* — drag the moved page anywhere on the base
- Exact **X / Y** offset spin-boxes (with `−` / `+` steppers) and a **Scale** control
- **Center** button to snap the moved page to the middle, **Reset crop** to restore full size
- **Undo / Redo** for every change (`Ctrl+Z` / `Ctrl+Y`)
- **Quality (DPI)** — reads the document's native resolution from its embedded
  images and uses it automatically (**Auto**), or set a value manually
- Output is a single-page PDF the same size as the base page (`Ctrl+S`)

### How it works

Both pages are normalized through a resolution-aware render (rotation and
non-zero MediaBox origins applied exactly as shown in the preview), so the
saved page matches the preview even for scanned or rotated PDFs. The moved page
is then cropped, scaled, and placed onto the base page. Auto quality detects the
native DPI from the largest embedded image (`pixels ÷ displayed inches`).

Default output name from the save dialog: `<name>_merged.pdf`.

### Shortcuts

| Action | Shortcut |
| --- | --- |
| Open PDF | `Ctrl+O` |
| Save PDF | `Ctrl+S` |
| Undo | `Ctrl+Z` |
| Redo | `Ctrl+Y` |

## Redact_PDF.py — permanent redaction

Draws redaction boxes on a PDF and writes a copy with everything under them
removed from the file, not just hidden behind a black rectangle.

```bash
# GUI
.venv/bin/python Redact_PDF.py

# Open a file straight away
.venv/bin/python Redact_PDF.py input.pdf
```

### Features

- Drag and drop a PDF (or click **Open PDF**, or press `Ctrl+O`)
- **Continuous scrolling** — the whole document is one tall canvas, so you can
  scroll from page 1 to the end and draw on any page without switching pages
- **Drag on a page to draw a redaction box**; drag a box to move it, drag its
  corner/edge handles to resize it, double-click or press `Del` to remove it.
  Boxes belong to the page they were drawn on and are clamped to it
- **Live preview** that scrolls too: a column of every page as it will be saved,
  produced by actually applying the redactions to a throwaway copy, so what you
  see is what gets saved. **Sync preview** keeps it aligned with the editor
- Page box (`PgUp` / `PgDown`, or type a page number) jumps both panels, and the
  **Copy to all pages** button repeats the current page's boxes everywhere —
  handy for headers, stamps, or signatures
- **Fill** (Black / White / No fill), **Images** (black out pixels / remove the
  image / keep), and **Vector art** (remove if covered / if touched / keep)
- Editor **zoom** (Fit width, 50–300%) for precise box placement
- **Undo / Redo** for every change (`Ctrl+Z` / `Ctrl+Y`), plus **Clear page** and
  **Clear all**
- Saves as `<name>_redacted.pdf` (`Ctrl+S`)

### How it works

Each box becomes a PyMuPDF redaction annotation (`add_redact_annot`) which is
then applied with `apply_redactions`, so the covered text, image pixels, and
vector art are deleted from the page's content stream — copying text out of the
result, or inspecting it with another tool, finds nothing. Everything outside the
boxes stays untouched vector content.

Boxes are stored in each page's *displayed* coordinate space (what the preview
shows) and converted with the page's derotation matrix before being applied, so
rotated pages and pages with an offset cropbox redact exactly where you drew.

Both panels only rasterize what is on screen plus a one-screen margin, and drop
bitmaps that scroll away, so a 300-page document opens in well under a second
and stays responsive. Very tall pages (such as the output of
`Long_PDF_Maker.py`) are rasterized in bands, so they stay sharp instead of
being downscaled to fit a single bitmap.

> Redaction is destructive by design. The source file is never modified — the
> result is always written to a new file.

### Shortcuts

| Action | Shortcut |
| --- | --- |
| Open PDF | `Ctrl+O` |
| Save redacted PDF | `Ctrl+S` |
| Delete selected box | `Del` |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Y` |
| Jump to previous / next page | `PgUp` / `PgDown` |

## Markdown_to_PDF.py — Markdown → styled PDF

Converts a Markdown file to a styled PDF suitable for cheat sheets and technical docs.

```bash
# GUI
.venv/bin/python Markdown_to_PDF.py

# CLI
.venv/bin/python Markdown_to_PDF.py input.md
.venv/bin/python Markdown_to_PDF.py input.md -o out.pdf --page-size A4 --orientation portrait --scale 1.25
```

### Features

- Drag-and-drop `.md` / `.markdown` files, or `Ctrl+O` to open
- In-place editor — edits re-render the PDF preview after a short debounce
- Live rendered preview (all pages) on the right, paginated just like the final output
- **Page size** (Letter / Legal / Tabloid / A3 / A4 / A5) and **Orientation** (landscape / portrait) selectors
- **Scale** control (50–300%) that zooms all text, code, tables, and headings together via CSS `zoom`
- Anti-clip rules: long URLs, code tokens, images, and table cells wrap instead of overflowing the page
- Consistent dark-blue code-block background across plain `<pre>` and Pygments-highlighted blocks
- Save as PDF (`Ctrl+S`) or export the intermediate styled HTML

### Pipeline

1. `python-markdown` converts `.md` → HTML with `codehilite` + Pygments syntax highlighting
2. A configurable `@page { size; orientation }` rule plus `html { zoom }` is injected along with the app's CSS
3. Chrome/Chromium is launched headlessly (`--headless=new --print-to-pdf`) to render the final PDF

### CLI flags

| Flag | Description |
| --- | --- |
| `input.md` | Input Markdown file (omit to launch GUI) |
| `-o, --output` | Output PDF path |
| `--title` | Optional HTML/PDF title override |
| `--page-size` | `Letter` / `Legal` / `Tabloid` / `A3` / `A4` / `A5` |
| `--orientation` | `landscape` / `portrait` |
| `--scale` | Text-scale factor, e.g. `1.25` for 125% |
| `--keep-html` | Keep the intermediate styled HTML next to the PDF |
| `--gui` | Force GUI even when other args are given |

### Shortcuts

| Action | Shortcut |
| --- | --- |
| Open Markdown | `Ctrl+O` |
| Save PDF | `Ctrl+S` |

On macOS, `Ctrl` maps to `⌘`.

## Project layout

```
Long_PDF_Maker.py     # long-page stitcher GUI
Long_PDF_To_A4.py     # long PDF to A4 splitter GUI + CLI
Merge_Two_Pages.py    # two-pages-onto-one GUI
Redact_PDF.py         # redaction box editor GUI
Markdown_to_PDF.py    # Markdown → PDF CLI + GUI
README.md
```
