"""
File service for CV uploads — reused from TalentHive (pdfplumber extraction).
"""

import io
import logging

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
