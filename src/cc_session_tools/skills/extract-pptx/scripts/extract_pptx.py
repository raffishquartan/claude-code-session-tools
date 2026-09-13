#!/usr/bin/env python3

"""Extract a .pptx file's full content: slide text, tables, speaker notes, OCR of
embedded images, embedded file objects, and (optionally) a sibling print-to-PDF.

Used by the `extract-pptx` skill. Exists because pandoc has a markdown *writer*
but no `.pptx` *reader* - there is no pandoc input format for PowerPoint, so any
pipeline that routes Office formats through `pandoc -t markdown` (a common,
otherwise-correct pattern for preserving Word/RTF tables) silently or loudly
fails on every .pptx file. This script is a standalone python-pptx-based
replacement for the .pptx case specifically.

Dependencies:
  python-pptx   pip install python-pptx
  Pillow        pip install pillow          (only needed for --ocr)
  pytesseract   pip install pytesseract     (only needed for --ocr)
  tesseract-ocr binary on PATH               (only needed for --ocr; apt/brew/winget package)
  soffice (LibreOffice) on PATH              (only needed for --pdf-out)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

EMBED_RELTYPE_MARKERS = ("package", "oleObject")


def shape_text(shape) -> list[str]:
    """Return this shape's own text content as a list of lines. Recurses into
    groups; handles plain text frames and tables. Does not touch images."""
    lines: list[str] = []

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for sub in shape.shapes:
            lines.extend(shape_text(sub))
        return lines

    if getattr(shape, "has_table", False) and shape.has_table:
        table = shape.table
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
        return lines

    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        text = shape.text_frame.text
        if text.strip():
            lines.extend(text.split("\n"))
        return lines

    return lines


def extract_slide_images(slide) -> list[tuple[str, bytes, str]]:
    """Return (shape_name, image_bytes, image_ext) for every picture shape on
    this slide, recursing into groups."""
    found: list[tuple[str, bytes, str]] = []

    def walk(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(shape.shapes)
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image = shape.image
                found.append((shape.name, image.blob, image.ext))

    walk(slide.shapes)
    return found


def extract_slide_embeds(slide) -> list[tuple[str, bytes]]:
    """Return (filename, bytes) for every embedded OLE/package object
    (embedded workbooks, documents, etc.) referenced by this slide."""
    found: list[tuple[str, bytes]] = []
    for rel in slide.part.rels.values():
        if rel.is_external:
            continue
        if not any(marker in rel.reltype for marker in EMBED_RELTYPE_MARKERS):
            continue
        target = rel.target_part
        fname = Path(str(target.partname)).name
        found.append((fname, target.blob))
    return found


def ocr_image(image_bytes: bytes) -> str:
    import io

    from PIL import Image
    import pytesseract

    img = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(img).strip()


def render_pdf(src: Path, pdf_out: Path, timeout: int) -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        sys.exit(
            "ERROR: --pdf-out requested but no `soffice` / `libreoffice` binary found on PATH. "
            "Install LibreOffice, or run this script inside a container that has it. "
            "Refusing to silently skip the PDF - report this to the user."
        )
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            capture_output=True, text=True, timeout=timeout,
        )
        produced = Path(tmp) / (src.stem + ".pdf")
        if result.returncode != 0 or not produced.exists():
            sys.exit(
                f"ERROR: soffice failed to render PDF.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
        pdf_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(pdf_out))


def main() -> int:
    p = argparse.ArgumentParser(description="Extract text, tables, notes, OCR, embeds, and (optionally) a sibling PDF from a .pptx file.")
    p.add_argument("pptx", type=Path, help="Path to the source .pptx file.")
    p.add_argument("--out-md", type=Path, required=True, help="Output markdown path.")
    p.add_argument("--pdf-out", type=Path, default=None, help="Also render a sibling PDF at this path (requires soffice/LibreOffice).")
    p.add_argument("--embeds-dir", type=Path, default=None, help="Directory to extract embedded OLE/package objects into. Skipped if none are found.")
    p.add_argument("--no-ocr", action="store_true", help="Skip OCR of embedded images (default: OCR is attempted; missing tesseract is a hard error, not a silent skip).")
    p.add_argument("--timeout", type=int, default=180, help="Subprocess timeout in seconds for the PDF render step (default 180).")
    args = p.parse_args()

    if not args.pptx.exists():
        sys.exit(f"ERROR: source file does not exist: {args.pptx}")

    if not args.no_ocr:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError as e:
            sys.exit(f"ERROR: OCR requested (default) but a dependency is missing ({e}). "
                      "Install pillow + pytesseract, or pass --no-ocr to skip OCR explicitly.")
        if not shutil.which("tesseract"):
            sys.exit("ERROR: OCR requested (default) but the `tesseract` binary is not on PATH. "
                      "Install tesseract-ocr, or pass --no-ocr to skip OCR explicitly.")

    prs = Presentation(str(args.pptx))

    out_lines: list[str] = [f"# {args.pptx.name}", ""]
    total_embeds: list[tuple[int, str, Path]] = []

    for idx, slide in enumerate(prs.slides, start=1):
        out_lines.append(f"## Slide {idx}")
        out_lines.append("")

        body_lines = shape_text_all(slide)
        if body_lines:
            out_lines.extend(body_lines)
        else:
            out_lines.append("*(no extractable text on this slide)*")
        out_lines.append("")

        if not args.no_ocr:
            images = extract_slide_images(slide)
            for shape_name, blob, ext in images:
                text = ocr_image(blob)
                if text:
                    out_lines.append(f"**OCR text from image `{shape_name}` (slide {idx}):**")
                    out_lines.append("")
                    out_lines.append(text)
                    out_lines.append("")

        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                out_lines.append(f"**Speaker notes (slide {idx}):**")
                out_lines.append("")
                out_lines.append(notes)
                out_lines.append("")

        embeds = extract_slide_embeds(slide)
        for fname, blob in embeds:
            if args.embeds_dir:
                dest = args.embeds_dir / f"slide{idx:03d}-{fname}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
                total_embeds.append((idx, fname, dest))

    if total_embeds:
        out_lines.append("## Embedded file objects")
        out_lines.append("")
        for idx, fname, dest in total_embeds:
            out_lines.append(f"- Slide {idx}: `{fname}` -> `{dest}`")
        out_lines.append("")

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"OK: wrote {args.out_md} ({len(prs.slides)} slides, {len(total_embeds)} embed(s))")

    if args.pdf_out:
        render_pdf(args.pptx, args.pdf_out, args.timeout)
        print(f"OK: wrote {args.pdf_out}")

    return 0


def shape_text_all(slide) -> list[str]:
    lines: list[str] = []
    for shape in slide.shapes:
        lines.extend(shape_text(shape))
    return lines


if __name__ == "__main__":
    sys.exit(main())
