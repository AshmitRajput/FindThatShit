"""Convert supported document bytes into bounded plain text."""
import codecs
import io
import re
import zipfile

from docx import Document
from pypdf import PdfReader

MAX_EXTRACT_CHARS = 100_000
MAX_PDF_PAGES = 200
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024


class ExtractionError(Exception):
    def __init__(self, error_code, message):
        super().__init__(message)
        self.error_code = error_code


def _pdf_text(data):
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("EXTRACTION_FAILED", "The PDF is password protected.")
        return "\n".join(page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES])
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("EXTRACTION_FAILED", "Could not read the PDF.") from exc


def _docx_text(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ExtractionError("EXTRACTION_FAILED", "The document is too large to read.")
        document = Document(io.BytesIO(data))
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("EXTRACTION_FAILED", "Could not read the document.") from exc


def _txt_text(data):
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


EXTRACTORS = {
    "application/pdf": _pdf_text,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _docx_text,
    "text/plain": _txt_text,
}


def extract_text(mime_type, data):
    extractor = EXTRACTORS.get(mime_type)
    if extractor is None:
        raise ExtractionError("EXTRACTION_FAILED", "Unsupported file type.")
    text = extractor(data).replace("\x00", "")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if not text:
        raise ExtractionError("NO_TEXT_FOUND", "No readable text was found in the file.")
    return text[:MAX_EXTRACT_CHARS]
