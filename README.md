# J PDF Maker

Stitch a multi-page PDF into a single long-page PDF while preserving vector quality. Modern PyQt6 GUI with drag-and-drop, draggable header/footer guides, live preview, and a PNG exporter.

## Features

- Drag and drop a PDF (or click **Open PDF**, or press `Ctrl+O`)
- Auto-detects likely header and footer heights from the first page's text layout
- Draggable red (header) and green (footer) guides on the source preview with a thick, easy-to-grab hit band and round handles
- Spin-box fine-tuning with large `−` / `+` steppers; DPI selector scrolls in 100-step increments
- Live, HiDPI-sharp preview of the stitched long-page output while you adjust
- **Keep header on page 1** toggle — if enabled, page 1 keeps its header while all other pages still have it removed
- Save the result as a vector PDF (`Ctrl+S`) or as a PNG at the chosen DPI (`Ctrl+E`)
- Runs on Windows, macOS, and Linux (uses Qt's Fusion style for identical rendering everywhere)

## Install

Requires Python 3.10+.

```bash
pip install PyQt6 PyMuPDF
```

### Windows

```
py -m venv .venv
.venv\Scripts\pip install PyQt6 PyMuPDF
.venv\Scripts\python PDF_maker.py
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/pip install PyQt6 PyMuPDF
.venv/bin/python PDF_maker.py
```

> Note: the package on PyPI named `fitz` is unrelated. You only need `PyMuPDF` — it provides the real `fitz` module. If you already installed the wrong one, run `pip uninstall -y fitz frontend` first.

## How it works

For each page, the top and bottom are cropped by the chosen point values, then every cropped page is placed onto one tall page using PyMuPDF's `show_pdf_page` — vectors are preserved. With **Keep header on page 1** on, the first page skips the top crop.

Default output names (when using the save dialogs):

- `<name>_long_page_vector.pdf`
- `<name>_long_page_vector.png`

## Shortcuts

| Action | Shortcut |
| --- | --- |
| Open PDF | `Ctrl+O` |
| Save PDF | `Ctrl+S` |
| Save PNG | `Ctrl+E` |

On macOS, `Ctrl` maps to `⌘`.
