import io
import unittest
import zipfile
from unittest import mock

from docx import Document
from pypdf import PdfWriter

from resources.extractors import documents
from resources.extractors.documents import ExtractionError, extract_text

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"


def make_pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def make_docx():
    document = Document()
    document.add_paragraph("Hello docx world")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "left"
    table.rows[0].cells[1].text = "right"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class ExtractTextTests(unittest.TestCase):
    def test_txt_utf8_including_indian_languages(self):
        text = extract_text(TXT, "नमस्ते resume தமிழ்".encode("utf-8"))
        self.assertIn("नमस्ते", text)
        self.assertIn("தமிழ்", text)

    def test_txt_utf16_with_bom(self):
        self.assertEqual(extract_text(TXT, "hello".encode("utf-16")), "hello")

    def test_nul_bytes_removed(self):
        self.assertEqual(extract_text(TXT, b"a\x00b"), "ab")

    def test_docx_paragraphs_and_tables(self):
        text = extract_text(DOCX, make_docx())
        self.assertIn("Hello docx world", text)
        self.assertIn("left | right", text)

    def test_pdf_text(self):
        self.assertIn("Resume template", extract_text(PDF, make_pdf("Resume template")))

    def test_pdf_without_text_is_no_text_found(self):
        writer, buffer = PdfWriter(), io.BytesIO()
        writer.add_blank_page(width=72, height=72)
        writer.write(buffer)
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(PDF, buffer.getvalue())
        self.assertEqual(ctx.exception.error_code, "NO_TEXT_FOUND")

    def test_garbage_is_extraction_failed(self):
        for mime in (PDF, DOCX):
            with self.assertRaises(ExtractionError) as ctx:
                extract_text(mime, b"definitely not a document")
            self.assertEqual(ctx.exception.error_code, "EXTRACTION_FAILED")

    def test_empty_text_file_is_no_text_found(self):
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(TXT, b"   \n\n  ")
        self.assertEqual(ctx.exception.error_code, "NO_TEXT_FOUND")

    def test_unsupported_mime_type(self):
        with self.assertRaises(ExtractionError) as ctx:
            extract_text("image/png", b"x")
        self.assertEqual(ctx.exception.error_code, "EXTRACTION_FAILED")

    def test_output_is_capped(self):
        with mock.patch.object(documents, "MAX_EXTRACT_CHARS", 10):
            self.assertEqual(len(extract_text(TXT, b"a" * 50)), 10)

    def test_oversized_docx_archive_is_rejected(self):
        with mock.patch.object(documents, "MAX_DOCX_UNCOMPRESSED_BYTES", 1):
            with self.assertRaises(ExtractionError) as ctx:
                extract_text(DOCX, make_docx())
        self.assertEqual(ctx.exception.error_code, "EXTRACTION_FAILED")
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(make_docx())))
