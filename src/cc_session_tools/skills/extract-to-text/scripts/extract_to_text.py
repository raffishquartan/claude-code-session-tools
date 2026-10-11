#!/usr/bin/env python3

"""Extract a document to markdown text, one output file per input.

Used by the `extract-to-text` skill. Generalizes a document-extraction pipeline's original
bug: it used python-docx's `"\\n".join(p.text for p in doc.paragraphs)`, which returns only
top-level body paragraphs and never descends into tables - a table-only document produced an
empty mirror recorded as a clean success, silently losing all its content.

Two rules follow, and both are enforced below:
  1. `.docx`/`.rtf`/`.odt` go through `pandoc -t markdown`, which preserves tables.
     Never python-docx `.paragraphs`.
  2. An empty body is a FAILURE, not a success. Status becomes `empty-output-CHECK-THIS`,
     never `ok`. A loud wrong answer beats a quiet one.

PPTX
----
`.pptx`/`.pptm` do NOT go through pandoc - pandoc has a markdown *writer* but no `.pptx` *input
reader*. Both delegate to the sibling `extract-pptx` skill's script (one pptx extractor in this
codebase, not two) rather than reimplementing pptx extraction here.

EMAIL FORMATS (.eml / .msg)
----------------------------
Neither goes through pandoc - pandoc has no reader for either format.
  - `.eml` uses only the stdlib `email` module - no extra dependency.
  - `.msg` uses the third-party `extract-msg` package - Outlook's binary format has no
    stdlib reader.
  - If a message has no `text/plain` part, the `text/html` part is walked with stdlib
    `html.parser` rather than pandoc/BeautifulSoup, to avoid a new dependency.

DEPENDENCIES
------------
  pandoc       - macOS: `brew install pandoc`   Windows: `winget install --id JohnMacFarlane.Pandoc`
  poppler      - macOS: `brew install poppler`  Windows: `winget install --id oschwartz10612.Poppler`
                 (provides pdftotext; only needed for .pdf)
  textutil     - macOS built-in, nothing to install (only needed for legacy .doc; macOS only)
  openpyxl     - `pip install --user openpyxl`     (only needed for .xlsx)
  xlrd         - `pip install --user xlrd`         (only needed for legacy .xls)
  ocrmypdf     - macOS: `brew install ocrmypdf`    (only needed for scanned PDFs with no text
                 layer; uses tesseract)
  extract-msg  - `pip install --user extract-msg`  (only needed for .msg)
  python-pptx  - `pip install --user python-pptx`  (only needed for .pptx/.pptm, via the
                 sibling extract-pptx skill's script)

USAGE
-----
  python3 extract_to_text.py <source-file> [<source-file> ...] --out-dir <dir>
  python3 extract_to_text.py <source-file> --out-dir <dir> --ocr   # pptx: also OCR images

Known limitations (not extracted, documented rather than silently dropped): native Office
charts, SmartArt, slide/presentation comments, hyperlink targets, and text baked into a slide
layout/master background. See extract-pptx's own SKILL.md for the pptx-specific subset of
these.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

PANDOC_EXTS = {".docx", ".rtf", ".odt"}
DOC_EXT = ".doc"  # legacy binary Word: pandoc cannot read it, macOS textutil can
ZIP_MAGIC = b"PK\x03\x04"  # OOXML containers; a ".xls" that starts with this is really an .xlsx
PPTX_EXTS = {".pptx", ".pptm"}  # .pptm (macro-enabled) is the same OOXML format as .pptx
TEXT_EXTS = {".txt", ".md", ".html", ".htm", ".csv", ".json"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # CDFV2 compound file - real format of Outlook .msg
SHORTCUT_MAGIC = b"[InternetShortcut]"

_PPTX_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / "extract-pptx" / "scripts" / "extract_pptx.py"
)


def read_text_file(p: Path) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def extract_pptx_via_sibling_skill(p: Path, *, ocr: bool) -> str:
    """Delegate .pptx/.pptm extraction to the extract-pptx skill's script rather than
    reimplementing pptx handling here - see the module docstring's PPTX section."""
    if not _PPTX_SCRIPT.exists():
        raise RuntimeError(
            f"extract-pptx skill script not found at {_PPTX_SCRIPT} - "
            "the extract-pptx skill must be installed alongside extract-to-text"
        )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out_md = Path(tmp) / "out.md"
        args = [sys.executable, str(_PPTX_SCRIPT), str(p), "--out-md", str(out_md)]
        if not ocr:
            args.append("--no-ocr")
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"extract-pptx script failed: {result.stderr.strip()[:300]}")
        return out_md.read_text(encoding="utf-8")


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML -> text converter used only when an email has no `text/plain` part.
    Drops `<script>`/`<style>` content and inserts a line break at block-tag boundaries
    so paragraphs don't run together. Stdlib-only (no BeautifulSoup)."""
    _BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                   "blockquote", "table"}
    _SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        lines = [line.strip() for line in "".join(self._chunks).splitlines()]
        out: list[str] = []
        for line in lines:
            if line or (out and out[-1] != ""):
                out.append(line)
        return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def _compose_email_markdown(sender, to, cc, date, subject, body: str, attachments: list[str]) -> str:
    out = [
        f"**From:** {sender or ''}",
        f"**To:** {to or ''}",
        f"**Cc:** {cc or ''}",
        f"**Date:** {date or ''}",
        f"**Subject:** {subject or ''}",
        "",
        body.strip() if body and body.strip() else "*(no body text extracted)*",
    ]
    if attachments:
        out.append("")
        out.append("**Attachments:**")
        out.extend(f"- {name}" for name in attachments)
    return "\n".join(out)


def extract_eml_body(p: Path) -> str:
    """Parse an .eml with the stdlib `email` module: prefer the `text/plain` part,
    falling back to a text-stripped rendering of `text/html` if that's all there is."""
    import email
    from email import policy

    with open(p, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        body = ""
    elif body_part.get_content_type() == "text/html":
        body = html_to_text(body_part.get_content())
    else:
        body = body_part.get_content()

    attachments = [name for part in msg.iter_attachments()
                   if (name := part.get_filename())]

    return _compose_email_markdown(msg["from"], msg["to"], msg["cc"], msg["date"],
                                    msg["subject"], body, attachments)


def extract_msg_body(p: Path) -> str:
    """Parse an Outlook .msg with the `extract-msg` package: prefer the plain-text
    `.body`, falling back to a text-stripped rendering of `.htmlBody`."""
    try:
        import extract_msg
    except ImportError as e:
        raise RuntimeError(
            f"'{p.name}' looks like an Outlook .msg file but the extract-msg package "
            f"is not installed ({e}). Install it with: pip install --user extract-msg"
        ) from e

    # openMsg()'s declared return type (MSGFile) is narrower than the runtime object's real
    # attributes - the actual .body/.sender/etc. live on a subclass the type stubs don't
    # expose from this call site. Use getattr() rather than a suppression, since which
    # subclass comes back genuinely varies by message kind (a real ambiguity, not a false
    # positive to silence).
    m = extract_msg.openMsg(str(p))
    try:
        body = getattr(m, "body", None) or ""
        if not body.strip():
            html = getattr(m, "htmlBody", None)
            if html:
                if isinstance(html, bytes):
                    html = html.decode("utf-8", errors="replace")
                body = html_to_text(html)
            else:
                body = ""

        attachments: list[str] = []
        for a in getattr(m, "attachments", []):
            get_filename = getattr(a, "getFilename", None)
            name = get_filename() if get_filename else None
            if name:
                attachments.append(name)

        return _compose_email_markdown(
            getattr(m, "sender", None), getattr(m, "to", None), getattr(m, "cc", None),
            getattr(m, "date", None), getattr(m, "subject", None), body, attachments,
        )
    finally:
        m.close()


def _sheet_markdown(sheets) -> str:
    """Render (title, rows) pairs in the shared .xlsx/.xls shape: one `## Sheet: <name>` section
    per sheet, rows as `cell | cell | ...`, empty cells dropped."""
    out: list[str] = []
    for title, rows in sheets:
        out.append(f"## Sheet: {title}\n")
        for row in rows:
            vals = [str(v) for v in row if v is not None]
            if vals:
                out.append(" | ".join(vals))
    return "\n".join(out)


def extract_xlsx_body(path_or_file, name: str) -> str:
    try:
        import openpyxl
    except ImportError as e:
        raise RuntimeError(
            f"'{name}' requires the openpyxl package, which is not installed ({e}). "
            "Install it with: pip install --user openpyxl"
        ) from e
    wb = openpyxl.load_workbook(path_or_file, read_only=True, data_only=True)
    return _sheet_markdown((ws.title, ws.iter_rows(values_only=True)) for ws in wb.worksheets)


def extract_xls_body(p: Path) -> tuple[str, str]:
    """Legacy binary .xls via xlrd. Returns (body, method)."""
    with p.open("rb") as fh:
        if fh.read(4) == ZIP_MAGIC:
            # openpyxl rejects the .xls name, so hand it an open file object instead of the path.
            with p.open("rb") as xf:
                return extract_xlsx_body(xf, p.name), "openpyxl (sniffed: .xlsx mislabeled as .xls)"
    try:
        import xlrd
    except ImportError as e:
        raise RuntimeError(
            f"'{p.name}' requires the xlrd package, which is not installed ({e}). "
            "Install it with: pip install --user xlrd"
        ) from e
    wb = xlrd.open_workbook(str(p), on_demand=True)
    try:
        def _cell(c):
            # xlrd returns numbers as floats, dates as serial floats, booleans as 0/1 and errors
            # as codes; render each as openpyxl would, and drop empty cells like openpyxl's None.
            if c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) or c.value == "":
                return None
            if c.ctype == xlrd.XL_CELL_DATE:
                return xlrd.xldate.xldate_as_datetime(c.value, wb.datemode).isoformat()
            if c.ctype == xlrd.XL_CELL_BOOLEAN:
                return bool(c.value)
            if c.ctype == xlrd.XL_CELL_ERROR:
                return xlrd.error_text_from_code.get(c.value, f"#ERR{c.value}")
            if isinstance(c.value, float) and c.value.is_integer():
                return int(c.value)
            return c.value

        sheets = ((sh.name, ([_cell(c) for c in sh.row(i)] for i in range(sh.nrows)))
                  for sh in (wb.sheet_by_index(n) for n in range(wb.nsheets)))
        return _sheet_markdown(sheets), "xlrd"
    finally:
        wb.release_resources()


def extract_doc_body(p: Path) -> tuple[str, str]:
    """Legacy binary .doc via macOS textutil (plain text, tables flattened). A file textutil
    cannot read whose head is the OLE2 magic is a mislabeled Outlook .msg."""
    if not _which("textutil"):
        raise RuntimeError(
            f"'{p.name}' is a legacy .doc, which is only supported where macOS `textutil` is "
            "available, and it is not on PATH. Convert the file to .docx first (for example "
            "with LibreOffice) and extract that."
        )
    r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(p)],
                        capture_output=True, text=True, timeout=180)
    # textutil falls back to reading unrecognised input as plain text, so a non-Word OLE2 file
    # (an Outlook .msg) can "succeed" with NUL-ridden garbage rather than fail: treat both as
    # unreadable.
    if r.returncode != 0 or "\x00" in r.stdout:
        with p.open("rb") as fh:
            if fh.read(8) == OLE_MAGIC:
                return extract_msg_body(p), "extract-msg (sniffed: .msg mislabeled as .doc)"
        if r.returncode != 0:
            raise RuntimeError(f"textutil failed: {r.stderr.strip()[:300]}")
        raise RuntimeError(
            f"textutil returned binary garbage for '{p.name}', so it is not a readable Word "
            "document (corrupt, or not a .doc)."
        )
    return r.stdout, "textutil -convert txt"


def extract_pdf_body(p: Path) -> tuple[str, str]:
    if not _which("pdftotext"):
        raise RuntimeError(
            f"'{p.name}' requires pdftotext (poppler), which is not on PATH. Install it: "
            "macOS `brew install poppler`, Windows "
            "`winget install --id oschwartz10612.Poppler`."
        )
    r = subprocess.run(["pdftotext", "-layout", str(p), "-"],
                        capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {r.stderr.strip()[:300]}")
    if r.stdout.strip():
        return r.stdout, "pdftotext -layout"
    # No text layer: a scanned PDF. OCR it; --skip-text leaves any pages that do have text alone.
    if not _which("ocrmypdf"):
        raise RuntimeError(
            f"'{p.name}' has no text layer and OCR requires ocrmypdf, which is not on PATH. "
            "Install it: macOS `brew install ocrmypdf`."
        )
    with tempfile.TemporaryDirectory(prefix="extract_to_text_ocr_") as td:
        sidecar = Path(td) / "ocr.txt"
        r = subprocess.run(
            ["ocrmypdf", "--skip-text", "--sidecar", str(sidecar), str(p), str(Path(td) / "ocr.pdf")],
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"ocrmypdf failed: {r.stderr.strip()[:300]}")
        return sidecar.read_text(encoding="utf-8", errors="replace"), "ocrmypdf --skip-text --sidecar"


def extract(p: Path, *, ocr: bool = False):
    """Return (body_text, method). Raises RuntimeError on tool/dependency failure."""
    ext = p.suffix.lower()

    if ext in PPTX_EXTS:
        return extract_pptx_via_sibling_skill(p, ocr=ocr), "python-pptx"

    if ext == ".eml":
        return extract_eml_body(p), "email-stdlib"

    if ext == ".msg":
        return extract_msg_body(p), "extract-msg"

    if ext == DOC_EXT:
        return extract_doc_body(p)

    if ext in PANDOC_EXTS:
        # Trust the file's actual signature over its extension - a real Outlook .msg or a
        # Windows Internet Shortcut mislabeled with one of these extensions sent straight to
        # pandoc fails with an opaque "couldn't unpack docx container" error either way.
        with p.open("rb") as fh:
            head = fh.read(512)
        if head[:8] == OLE_MAGIC:
            return extract_msg_body(p), "extract-msg (sniffed: .msg mislabeled as " + ext + ")"
        if head.lstrip()[:len(SHORTCUT_MAGIC)] == SHORTCUT_MAGIC:
            raise RuntimeError(
                f"'{p.name}' is a Windows Internet Shortcut (.url) mislabeled as {ext}, not a "
                f"document - the real content is not present locally under this name. Shortcut "
                f"target: {head.decode('utf-8', 'replace').strip()[:200]}"
            )
        if not _which("pandoc"):
            raise RuntimeError(
                f"'{p.name}' requires pandoc, which is not on PATH. Install it: "
                "macOS `brew install pandoc`, Windows `winget install --id JohnMacFarlane.Pandoc`."
            )
        r = subprocess.run(["pandoc", "-t", "markdown", str(p)],
                            capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError(f"pandoc failed: {r.stderr.strip()[:300]}")
        return r.stdout, "pandoc -t markdown"

    if ext == ".pdf":
        return extract_pdf_body(p)

    if ext == ".xlsx":
        return extract_xlsx_body(str(p), p.name), "openpyxl"

    if ext == ".xls":
        return extract_xls_body(p)

    if ext in TEXT_EXTS:
        return read_text_file(p), "raw-text"

    raise RuntimeError(f"no extractor registered for '{ext}'")


def _which(binary: str) -> str | None:
    import shutil
    return shutil.which(binary)


def write_output(src: Path, out_dir: Path, *, ocr: bool = False) -> str:
    dst = out_dir / (src.name + ".md")

    try:
        body, method = extract(src, ocr=ocr)
        status = "ok"
    except Exception as e:
        body, method, status = "", "n/a", f"FAILED: {e}"

    # RULE 2: empty body is never 'ok'.
    if status == "ok" and not body.strip():
        status = "empty-output-CHECK-THIS"

    fm = [
        "---",
        f"source_path: {src}",
        f"ext: {src.suffix.lower().lstrip('.')}",
        f"extraction_method: {method}",
        f"extraction_status: {status}",
        f"extracted_at: {datetime.now(timezone.utc).isoformat()}",
        f"extractor: extract-to-text",
        "---",
        "",
    ]
    if status != "ok":
        fm += [f"> **⚠ EXTRACTION NOT CLEAN: `{status}`.** Do not read this file as evidence",
               "> that the source document is empty. Open the source directly and investigate.", ""]

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(fm) + body, encoding="utf-8")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path, help="Source document(s) to extract.")
    ap.add_argument("--out-dir", type=Path, required=True, help="Directory to write <name>.md into.")
    ap.add_argument("--ocr", action="store_true",
                    help="For .pptx/.pptm, also OCR image text (off by default - see extract-pptx).")
    args = ap.parse_args()

    counts: dict[str, int] = {}
    for p in args.files:
        if not p.exists():
            print(f"MISSING {p}", file=sys.stderr)
            counts["MISSING"] = counts.get("MISSING", 0) + 1
            continue
        status = write_output(p, args.out_dir, ocr=args.ocr)
        key = status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        print(f"{status:26} {p}")

    print(f"\n{counts}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
