from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF


"""
Vector-preserving PDF stitcher.

What it does:
- Asks the user for the input PDF location
- Crops top and bottom from every page
- Stitches all cropped pages into one long PDF page
- Saves the final long PDF
- Also saves the final long PDF as a PNG preview/export

User-adjustable defaults:
- DEFAULT_TOP_CROP_PT: top crop in PDF points
- DEFAULT_BOTTOM_CROP_PT: bottom crop in PDF points
- DEFAULT_PNG_PPI: PNG export resolution
"""

DEFAULT_TOP_CROP_PT = 26
DEFAULT_BOTTOM_CROP_PT = 32
DEFAULT_PNG_PPI = 300


def make_long_pdf_vector(
    input_pdf: str,
    output_pdf: str,
    top_crop_pt: float = DEFAULT_TOP_CROP_PT,
    bottom_crop_pt: float = DEFAULT_BOTTOM_CROP_PT,
) -> None:
    """Create one long-page PDF from a multi-page PDF while preserving vector quality."""
    src = fitz.open(input_pdf)

    if src.page_count == 0:
        src.close()
        raise ValueError("Input PDF has no pages.")

    clips: list[fitz.Rect] = []
    total_height = 0.0
    max_width = 0.0

    for page in src:
        r = page.rect
        clip = fitz.Rect(
            r.x0,
            r.y0 + top_crop_pt,
            r.x1,
            r.y1 - bottom_crop_pt,
        )

        if clip.height <= 0:
            src.close()
            raise ValueError(
                f"Crop removed entire page. Reduce crop values. "
                f"(top={top_crop_pt}, bottom={bottom_crop_pt})"
            )

        clips.append(clip)
        total_height += clip.height
        max_width = max(max_width, clip.width)

    out = fitz.open()
    dst_page = out.new_page(width=max_width, height=total_height)

    y = 0.0
    for page_num, clip in enumerate(clips):
        dest = fitz.Rect(0, y, clip.width, y + clip.height)
        dst_page.show_pdf_page(
            dest,
            src,
            page_num,
            clip=clip,
            keep_proportion=False,
            overlay=True,
        )
        y += clip.height

    out.save(output_pdf, garbage=4, deflate=True)
    out.close()
    src.close()


def export_pdf_first_page_to_png(
    pdf_path: str,
    png_path: str,
    ppi: int = DEFAULT_PNG_PPI,
) -> None:
    """
    Export the long single-page PDF as PNG.
    Since the output PDF is one long page, exporting page 0 exports the full result.
    """
    doc = fitz.open(pdf_path)
    if doc.page_count == 0:
        doc.close()
        raise ValueError("Output PDF has no pages to export.")

    page = doc[0]
    pix = page.get_pixmap(alpha=False, dpi=ppi)
    pix.save(png_path)
    doc.close()


def build_output_paths(input_pdf_path: Path) -> tuple[Path, Path]:
    stem = input_pdf_path.stem
    parent = input_pdf_path.parent
    output_pdf = parent / f"{stem}_long_page_vector.pdf"
    output_png = parent / f"{stem}_long_page_vector.png"
    return output_pdf, output_png


def main() -> None:
    input_path_str = input("Enter the full path to the input PDF: ").strip().strip('"')

    if not input_path_str:
        print("No PDF path provided.")
        sys.exit(1)

    input_path = Path(input_path_str)

    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)

    if input_path.suffix.lower() != ".pdf":
        print(f"Not a PDF file: {input_path}")
        sys.exit(1)

    output_pdf, output_png = build_output_paths(input_path)

    try:
        make_long_pdf_vector(
            input_pdf=str(input_path),
            output_pdf=str(output_pdf),
            top_crop_pt=DEFAULT_TOP_CROP_PT,
            bottom_crop_pt=DEFAULT_BOTTOM_CROP_PT,
        )

        export_pdf_first_page_to_png(
            pdf_path=str(output_pdf),
            png_path=str(output_png),
            ppi=DEFAULT_PNG_PPI,
        )

    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(f"Done.")
    print(f"PDF saved to: {output_pdf}")
    print(f"PNG saved to: {output_png}")
    print(f"PNG export resolution: {DEFAULT_PNG_PPI} PPI")


if __name__ == "__main__":
    main()
