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

    # Decompression bounds. The 5MB upload cap bounds the COMPRESSED
    # archive only: XML deflates ~1000x (measured), so z.read() can
    # materialise gigabytes from a small upload and OOM the single
    # Render worker. TWO doors had to close (review round 2):
    #   - per-part: one oversized part (a 4.9MB zip declaring a ~5GB
    #     document.xml);
    #   - TOTAL: many legal-sized parts (20 headers x 19.9MB = 398MB
    #     from a 0.374MB upload; the same OOM through a different door).
    # zipfile stops read() at the declared size and raises BadZipFile on
    # CRC mismatch, so the declared file_size is a sound bound.
    _MAX_DOCX_PART_BYTES = 20 * 1024 * 1024
    _MAX_DOCX_TOTAL_BYTES = 40 * 1024 * 1024
    # A real document references at most a handful of header/footer
    # parts (first/even/odd per section); more means junk padding.
    _MAX_DOCX_PART_COUNT = 12

    @staticmethod
    def extract_text_from_docx(file_content: bytes) -> str:
        """Text from a .docx with ONLY the standard library — no new
        dependency.

        Parts read: word/document.xml (the body) plus ONLY the
        header/footer parts its sections actually reference (resolved
        through word/_rels/document.xml.rels). Word CV templates
        routinely put the contact block (name, email, phone) in the
        page header; reference resolution skips orphaned parts (left
        behind by template switches) and first/even/odd copies of the
        same block are additionally de-duplicated by text, so the
        contact block lands once, not 2-3x, at the top of cv_text.

        Fidelity guards on the paragraph walk:
        - w:tab emits a tab, w:br/w:cr a newline: Word headers are
          built from tab stops and breaks, and without separators the
          contact block arrives at extract_profile as one glued token
          ('Anna Anderssonanna@example.com...') — unparseable;
        - nested <w:p> (text boxes) is emitted ONCE: each paragraph's
          run sweep skips nested paragraph subtrees, which root.iter
          visits on their own pass — without this, two-column templates
          duplicate whole sections into cv_text and every AI prompt;
        - mc:Fallback subtrees are dropped entirely: Word writes text
          boxes twice (mc:Choice drawing + mc:Fallback VML copy for old
          readers), so keeping both would double every boxed section.

        Memory: parts are streamed (read -> parse -> drop bytes) under
        per-part, total, and part-count budgets, never materialised as
        a list of decompressed blobs.

        Any failure — missing part, encrypted archive (RuntimeError),
        unsupported compression (NotImplementedError), bad XML, bomb,
        or no text at all — becomes ValueError, the upload path's 400
        shape. The PDF sibling wraps broadly for the same reason: a
        500 from here surfaces in the browser as a CORS-less network
        error, which helps nobody.
        """
        import posixpath
        import xml.etree.ElementTree as ET

        W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

        budget = {"total": 0, "parts": 0}

        def part_bytes(z: zipfile.ZipFile, name: str) -> bytes:
            # Declared sizes bound z.read() BEFORE it happens; the
            # running total closes the many-small-parts door.
            declared = z.getinfo(name).file_size
            if declared > FileService._MAX_DOCX_PART_BYTES:
                raise ValueError(
                    f"Word document part '{name}' expands beyond "
                    f"{FileService._MAX_DOCX_PART_BYTES // (1024 * 1024)}MB — "
                    "file refused"
                )
            budget["total"] += declared
            budget["parts"] += 1
            if budget["total"] > FileService._MAX_DOCX_TOTAL_BYTES:
                raise ValueError(
                    "Word document parts expand beyond "
                    f"{FileService._MAX_DOCX_TOTAL_BYTES // (1024 * 1024)}MB "
                    "in total — file refused"
                )
            if budget["parts"] > FileService._MAX_DOCX_PART_COUNT:
                raise ValueError("Word document has too many parts — file refused")
            return z.read(name)

        def paragraphs_from(root) -> list[str]:

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
                    elif child.tag == f"{W}tab":
                        parts.append("\t")
                    elif child.tag in (f"{W}br", f"{W}cr"):
                        parts.append("\n")
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

        def referenced_furniture(z: zipfile.ZipFile, document_root) -> list[str]:
            """Header/footer part names the document's sections actually
            reference, via the package relationships — orphans and
            unreferenced copies stay unread."""
            rels_name = "word/_rels/document.xml.rels"
            try:
                if rels_name not in z.namelist():
                    return []
                rels_root = ET.fromstring(part_bytes(z, rels_name))
            except ET.ParseError:
                return []
            id_to_target = {
                el.get("Id"): el.get("Target")
                for el in rels_root
                if el.get("Id") and el.get("Target")
            }
            names: list[str] = []
            for ref in document_root.iter():
                if not (ref.tag.endswith("}headerReference") or ref.tag.endswith("}footerReference")):
                    continue
                target = id_to_target.get(ref.get(f"{R}id"))
                if target:
                    names.append(posixpath.normpath(posixpath.join("word", target)))
            return list(dict.fromkeys(names))  # stable de-dup by name

        header_blocks: list[str] = []
        footer_blocks: list[str] = []
        body_blocks: list[str] = []
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as z:
                doc_root = ET.fromstring(part_bytes(z, "word/document.xml"))
                body_blocks = paragraphs_from(doc_root)
                for name in referenced_furniture(z, doc_root):
                    blocks = paragraphs_from(ET.fromstring(part_bytes(z, name)))
                    seen, target = set(), header_blocks if "header" in name else footer_blocks
                    for b in blocks:
                        # first/even/odd variants carry the same contact
                        # text — identical blocks land once
                        if b not in seen and b not in target:
                            seen.add(b)
                            target.append(b)
        except ValueError:
            raise
        except Exception as e:
            # BadZipFile, KeyError (missing part), RuntimeError (encrypted
            # archive), NotImplementedError (Deflate64) — all 400s.
            raise ValueError("File is not a valid Word document (.docx)") from e

        blocks = header_blocks + body_blocks + footer_blocks
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
