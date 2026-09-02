"""
File service for CV uploads — reused from TalentHive (pdfplumber extraction).
"""

import io
import logging
import zipfile

import pdfplumber

logger = logging.getLogger(__name__)


class FileService:
    """Service for handling file operations."""

    @staticmethod
    def extract_text_from_pdf(file_content: bytes) -> str:
        """
        Extract text content from a PDF file.

        Args:
            file_content: Raw PDF file bytes

        Returns:
            Extracted text content

        Raises:
            ValueError: If text extraction fails
        """
        try:
            text_parts = []
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

            full_text = "\n\n".join(text_parts)

            if not full_text.strip():
                raise ValueError("No text could be extracted from the PDF")

            logger.info("Successfully extracted %d characters from PDF", len(full_text))
            return full_text

        except Exception as e:
            logger.error("PDF text extraction failed: %s", e)
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")

    @staticmethod
    @staticmethod
    def validate_size(file_content: bytes, max_size_mb: int = 5) -> None:
        """Size gate shared by every accepted CV format."""
        size_mb = len(file_content) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise ValueError(
                f"File size ({size_mb:.2f}MB) exceeds maximum ({max_size_mb}MB)"
            )

    @staticmethod
    def is_docx(file_content: bytes) -> bool:
        """True for a ZIP structured as a Word .docx ([Content_Types].xml
        plus a word/ part). Guards the upload branch against renamed
        junk — a .docx name with non-ZIP bytes fails here and gets a
        clear error instead of a PDF-parser traceback."""
        if not file_content.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as z:
                names = z.namelist()
                return "[Content_Types].xml" in names and any(
                    n.startswith("word/") for n in names
                )
        except zipfile.BadZipFile:
            return False

    @staticmethod
    def extract_text_from_docx(file_content: bytes) -> str:
        """Text from a .docx with ONLY the standard library — no new
        dependency. word/document.xml is WordprocessingML: every <w:p>
        is a paragraph, <w:t> elements hold the runs (table cells are
        paragraphs too, so table text comes along in document order).

        Raises ValueError (the upload path's 400 shape) on anything
        that isn't a readable docx or yields no text.
        """
        import xml.etree.ElementTree as ET

        W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as z:
                xml_bytes = z.read("word/document.xml")
        except (KeyError, zipfile.BadZipFile) as e:
            raise ValueError("File is not a valid Word document (.docx)") from e
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            raise ValueError("File is not a valid Word document (.docx)") from e

        paragraphs = []
        for para in root.iter(f"{W}p"):
            text = "".join(t.text or "" for t in para.iter(f"{W}t"))
            if text:
                paragraphs.append(text)
        full_text = "\n".join(paragraphs)
        if not full_text.strip():
            raise ValueError("No text could be extracted from the document")
        return full_text

    def validate_pdf(file_content: bytes, max_size_mb: int = 5) -> bool:
        """
        Validate a PDF file (size + header) — TalentHive logic reused.

        Raises:
            ValueError: If validation fails
        """
        size_mb = len(file_content) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise ValueError(f"File size ({size_mb:.2f}MB) exceeds maximum ({max_size_mb}MB)")

        if not file_content.startswith(b"%PDF"):
            raise ValueError("File is not a valid PDF")

        return True
