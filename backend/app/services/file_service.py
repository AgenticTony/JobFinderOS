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
    def validate_size(file_content: bytes, max_size_mb: int = 5) -> None:
        """Size gate shared by every accepted CV format (bounds the
        COMPRESSED upload; per-part decompression is bounded separately
        in extract_text_from_docx — see the zip-bomb note there)."""
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
        clear error instead of a parser traceback."""
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

    # Bound on the DECOMPRESSED size of any single docx XML part. The
    # 5MB upload cap bounds the compressed archive only: XML deflates
    # ~1000x (measured), so a 4.9MB zip can declare a multi-GB
    # word/document.xml and OOM the single Render worker at z.read().
    # A real CV's largest part runs a few hundred KB; 20MB is generous.
    _MAX_DOCX_PART_BYTES = 20 * 1024 * 1024

    @staticmethod
    def extract_text_from_docx(file_content: bytes) -> str:
        """Text from a .docx with ONLY the standard library — no new
        dependency.

        Parts read: word/document.xml (the body) plus word/header*.xml
        and word/footer*.xml — Word CV templates routinely put the
        contact block (name, email, phone) in the page header, and
        without those parts the AI profile extraction sees no contact
        block at all.

        Fidelity guards on the paragraph walk:
        - nested <w:p> (text boxes) is emitted ONCE: each paragraph's
          run sweep skips nested paragraph subtrees, which root.iter
          visits on their own pass — without this, two-column templates
          duplicate whole sections into cv_text and every AI prompt;
        - mc:Fallback subtrees are dropped entirely: Word writes text
          boxes twice (mc:Choice drawing + mc:Fallback VML copy for old
          readers), so keeping both would double every boxed section.

        Any failure — missing part, encrypted archive (RuntimeError),
        unsupported compression (NotImplementedError), bad XML, bomb,
        or no text at all — becomes ValueError, the upload path's 400
        shape. The PDF sibling wraps broadly for the same reason: a
        500 from here surfaces in the browser as a CORS-less network
        error, which helps nobody.
        """
        import re as _re
        import xml.etree.ElementTree as ET

        W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

        def part_bytes(z: zipfile.ZipFile, name: str) -> bytes:
            # The declared uncompressed size bounds z.read's
            # materialisation before it happens.
            if z.getinfo(name).file_size > FileService._MAX_DOCX_PART_BYTES:
                raise ValueError(
                    f"Word document part '{name}' expands beyond "
                    f"{FileService._MAX_DOCX_PART_BYTES // (1024 * 1024)}MB — "
                    "file refused"
                )
            return z.read(name)

        def paragraphs_from(xml: bytes) -> list[str]:
            root = ET.fromstring(xml)

            # Elements inside mc:Fallback are duplicates by design —
            # id-set them once, skip everywhere below (ET has no parent
            # pointers, so identity sets are the cheap exclusion).
            fallback_ids: set[int] = set()
            for fb in root.iter(FALLBACK):
                fallback_ids.update(id(el) for el in fb.iter())

            def runs_text(node) -> str:
                parts: list[str] = []
                for child in node:
                    if id(child) in fallback_ids or child.tag == FALLBACK:
                        continue
                    if child.tag == f"{W}p":
                        # nested paragraph — its own iter pass collects it
                        continue
                    if child.tag == f"{W}t":
                        parts.append(child.text or "")
                    parts.append(runs_text(child))
                return "".join(parts)

            out = []
            for para in root.iter(f"{W}p"):
                if id(para) in fallback_ids:
                    continue
                text = runs_text(para)
                if text:
                    out.append(text)
            return out

        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as z:
                names = z.namelist()
                headers = sorted(n for n in names if _re.fullmatch(r"word/header\d*\.xml", n))
                footers = sorted(n for n in names if _re.fullmatch(r"word/footer\d*\.xml", n))
                parts = [(n, part_bytes(z, n)) for n in [*headers, "word/document.xml", *footers]]
        except ValueError:
            raise
        except Exception as e:
            # BadZipFile, KeyError (missing part), RuntimeError (encrypted
            # archive), NotImplementedError (Deflate64) — all 400s.
            raise ValueError("File is not a valid Word document (.docx)") from e

        try:
            blocks = [p for _, xml in parts for p in paragraphs_from(xml)]
        except ET.ParseError as e:
            raise ValueError("File is not a valid Word document (.docx)") from e

        full_text = "\n".join(blocks)
        if not full_text.strip():
            raise ValueError("No text could be extracted from the document")
        return full_text

    @staticmethod
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
