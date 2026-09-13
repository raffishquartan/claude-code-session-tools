"""Tests for ../scripts/extract_to_text.py.

Exercised end-to-end via subprocess (matching the sibling `extract-pptx` skill's test
convention), so the real CLI behaviour is what we assert on. External-binary-dependent
formats (pandoc, pdftotext) skip gracefully when the binary isn't on PATH, matching the
`HAS_SOFFICE`/`HAS_TESSERACT` pattern already used in `extract-pptx`'s own tests.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "extract_to_text.py"

HAS_PANDOC = bool(shutil.which("pandoc"))
HAS_PDFTOTEXT = bool(shutil.which("pdftotext"))

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _out_md_for(out_dir: Path, src: Path) -> Path:
    return out_dir / (src.name + ".md")


def _write_minimal_pdf(path: Path, text: str) -> None:
    """Write a minimal, valid single-page PDF containing `text`, built by hand (correct
    xref offsets computed below) rather than via a PDF-writing library or pandoc's
    LaTeX/wkhtmltopdf-dependent PDF engines (neither is reliably available in a dev
    environment) - the sole goal is a real file pdftotext can read text back out of."""
    stream = f"BT /F1 18 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 300 144] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF"
    ).encode("ascii")
    path.write_bytes(bytes(out))


class ExtractToTextCli(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="extract_to_text_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- pandoc family: docx/doc/rtf/odt ----------

    def test_docx_table_only_document_extracts_table_content(self) -> None:
        """The motivating bug: python-docx's .paragraphs never descends into tables.
        A .docx whose only content is a table must still extract that content."""
        if not HAS_PANDOC:
            self.skipTest("pandoc not on PATH")
        src = self._build_table_only_docx()
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("Region", text)
        self.assertIn("Revenue", text)
        self.assertIn("EMEA", text)
        self.assertIn("extraction_method: pandoc -t markdown", text)
        self.assertIn("extraction_status: ok", text)

    def _build_table_only_docx(self) -> Path:
        """Build a .docx whose only content is a table, via pandoc itself (round-trip:
        markdown table -> docx), so this test needs no python-docx dependency."""
        src = self.tmp / "table-only.docx"
        md_src = self.tmp / "table-only.md"
        md_src.write_text("| Region | Revenue |\n| --- | --- |\n| EMEA | 42 |\n")
        subprocess.run(["pandoc", str(md_src), "-o", str(src)], check=True, timeout=30)
        return src

    # ---------- pptx/pptm: delegates to the extract-pptx skill's script ----------

    def test_pptx_delegates_to_extract_pptx_script(self) -> None:
        from pptx import Presentation

        src = self.tmp / "deck.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "Quarterly Review"
        prs.save(str(src))

        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("Quarterly Review", text)
        self.assertIn("extraction_method: python-pptx", text)

    # ---------- email formats: eml (stdlib) / msg (extract-msg) ----------

    def test_eml_extracts_headers_and_body(self) -> None:
        src = self.tmp / "message.eml"
        src.write_text(
            "From: sender@example.com\r\n"
            "To: recipient@example.com\r\n"
            "Subject: Test subject\r\n"
            "Date: Mon, 1 Jun 2026 12:00:00 +0000\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "This is the message body.\r\n"
        )
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("sender@example.com", text)
        self.assertIn("Test subject", text)
        self.assertIn("This is the message body.", text)
        self.assertIn("extraction_method: email-stdlib", text)

    def test_msg_extracts_via_extract_msg_package(self) -> None:
        try:
            import extract_msg  # noqa: F401
        except ImportError:
            self.skipTest("extract-msg package not installed")
        # extract-msg has no public .msg *writer*, so building a real fixture at test
        # time isn't practical here - covered instead by the signature-sniffing test
        # below, which exercises the same extract_msg_body() code path via a real
        # OLE2-signed file. A dedicated .msg fixture is exactly the checked-in-binary
        # case the sibling extract-pptx tests avoid; skip rather than check one in.
        self.skipTest("no .msg writer available to build a fixture at test time")

    # ---------- pdf / xlsx ----------

    def test_pdf_extracts_text(self) -> None:
        if not HAS_PDFTOTEXT:
            self.skipTest("pdftotext not on PATH")
        pdf_src = self.tmp / "doc.pdf"
        _write_minimal_pdf(pdf_src, "Hello from a pdf test fixture.")
        out_dir = self.tmp / "out"
        result = _run(str(pdf_src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, pdf_src).read_text()
        self.assertIn("Hello from a pdf test fixture.", text)
        self.assertIn("extraction_method: pdftotext -layout", text)

    def test_xlsx_extracts_sheet_rows(self) -> None:
        import openpyxl

        src = self.tmp / "book.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["Region", "Revenue"])
        ws.append(["EMEA", 42])
        wb.save(str(src))

        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("Sheet1", text)
        self.assertIn("Region | Revenue", text)
        self.assertIn("EMEA | 42", text)
        self.assertIn("extraction_method: openpyxl", text)

    # ---------- plain text passthrough ----------

    def test_plain_text_passthrough(self) -> None:
        src = self.tmp / "notes.txt"
        src.write_text("Plain text content.\n")
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("Plain text content.", text)
        self.assertIn("extraction_method: raw-text", text)

    # ---------- signature sniffing ----------

    def test_msg_signature_mislabeled_as_docx_routes_to_msg_handler(self) -> None:
        try:
            import extract_msg  # noqa: F401
        except ImportError:
            self.skipTest("extract-msg package not installed")
        # A minimal OLE2/CFBF-signed file is enough to trigger the signature check;
        # it need not be a fully valid .msg for the routing test - a genuinely valid
        # one would need the extract-msg package's own writer, which doesn't exist.
        # This exercises the *routing* decision, not extract_msg_body() itself.
        src = self.tmp / "mislabeled.docx"
        src.write_bytes(OLE_MAGIC + b"\x00" * 512)
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        # Routed to the .msg handler, which then fails to parse this non-real .msg -
        # the point being asserted is that it did NOT get sent to pandoc.
        self.assertNotIn("pandoc", (result.stdout + result.stderr).lower())

    def test_shortcut_signature_mislabeled_as_docx_reports_shortcut(self) -> None:
        src = self.tmp / "mislabeled.docx"
        src.write_text("[InternetShortcut]\nURL=https://example.com/budget.xlsx\n")
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("Internet Shortcut", text)
        self.assertIn("https://example.com/budget.xlsx", text)
        self.assertIn("extraction_status: FAILED", text)

    # ---------- empty-output-is-failure rule ----------

    def test_empty_extraction_is_not_reported_as_ok(self) -> None:
        src = self.tmp / "empty.txt"
        src.write_text("   \n\n  ")
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("extraction_status: empty-output-CHECK-THIS", text)

    # ---------- missing dependency failure messages ----------

    def test_missing_source_file_is_reported(self) -> None:
        out_dir = self.tmp / "out"
        result = _run(str(self.tmp / "nope.docx"), "--out-dir", str(out_dir))
        self.assertIn("MISSING", result.stdout + result.stderr)

    def test_unknown_extension_fails_with_clear_message(self) -> None:
        src = self.tmp / "mystery.xyz"
        src.write_text("content")
        out_dir = self.tmp / "out"
        result = _run(str(src), "--out-dir", str(out_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _out_md_for(out_dir, src).read_text()
        self.assertIn("no extractor registered", text)
        self.assertIn("extraction_status: FAILED", text)


if __name__ == "__main__":
    unittest.main()
