# J PDF Maker

Two Python/PyQt6 tools for making PDFs look the way you want:

- **PDF_maker.py** — stitch a multi-page PDF into a single long-page PDF while preserving vector quality, with draggable header/footer guides and a live preview.
- **Markdown_to_PDF.py** — convert Markdown to a styled PDF (colored code blocks, wrapped lines, configurable page size / orientation / text scale) with a live rendered preview.

Both apps share the same dark Catppuccin-style UI: drag-and-drop source panel on the left, live preview on the right, and a row of colored-chip controls at the bottom.

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

## PDF_maker.py — long-page stitcher

Stitches a multi-page PDF into one tall page, keeping everything vector.

```bash
.venv/bin/python PDF_maker.py
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
PDF_maker.py          # long-page stitcher GUI
Markdown_to_PDF.py    # Markdown → PDF CLI + GUI
README.md
```
