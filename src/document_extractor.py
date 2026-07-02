"""
document_extractor.py — Clinical Document Text Extraction Service

Supports:
  - Native-text PDFs   → pypdf  (pure Python, no system deps)
  - Scanned PDFs       → pypdf page-by-page; falls back to Tesseract if text is blank
  - PNG / JPG / TIFF   → Pillow + pytesseract OCR

The module is intentionally side-effect-free: all functions are pure or take
explicit arguments. No global state. The FastAPI endpoint owns the lifecycle.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger("document_extractor")

# Maximum characters of extracted text forwarded to the LLM.
# Keeps prompts under ~1 K tokens while preserving the most relevant header
# section that typically contains patient/admission/diagnosis data.
_MAX_CHARS = 4000


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(data: bytes) -> str:
    """
    Extract raw text from a PDF byte payload using pypdf.

    Returns empty string on failure rather than raising — callers decide
    whether to fall back to Tesseract or surface an error.
    """
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)

        raw = "\n".join(parts).strip()
        logger.debug("pypdf extracted %d chars from %d pages", len(raw), len(reader.pages))
        return raw

    except ImportError:
        logger.error("pypdf is not installed — cannot extract PDF text")
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    except Exception as exc:
        logger.warning("pypdf extraction failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Image OCR
# ---------------------------------------------------------------------------

def extract_text_from_image(data: bytes) -> str:
    """
    OCR an image (PNG/JPG/TIFF) via pytesseract + Pillow.

    Raises RuntimeError with a clear message if Tesseract is not installed.
    """
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore

        image = Image.open(io.BytesIO(data))
        raw = pytesseract.image_to_string(image, lang="eng").strip()
        logger.debug("pytesseract extracted %d chars", len(raw))
        return raw

    except ImportError as exc:
        raise RuntimeError(
            "Image OCR requires Pillow and pytesseract. "
            "Run: pip install Pillow pytesseract — and install the Tesseract binary: "
            "winget install UB-Mannheim.TesseractOCR"
        ) from exc
    except Exception as exc:
        logger.warning("pytesseract OCR failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def route_extraction(filename: str, data: bytes) -> str:
    """
    Route to the appropriate extractor based on file extension.

    For scanned PDFs where pypdf returns empty text, automatically falls
    back to Tesseract if available.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        text = extract_text_from_pdf(data)
        if not text.strip():
            logger.info("pypdf returned empty text — attempting image OCR fallback for scanned PDF")
            try:
                text = _ocr_pdf_as_images(data)
            except Exception as exc:
                logger.warning("Image OCR fallback failed: %s", exc)
        return text

    elif ext in ("png", "jpg", "jpeg", "tiff", "tif", "webp", "bmp"):
        return extract_text_from_image(data)

    else:
        raise ValueError(
            f"Unsupported file type '.{ext}'. "
            "Accepted: pdf, png, jpg, jpeg, tiff, tif, webp, bmp"
        )


def _ocr_pdf_as_images(data: bytes) -> str:
    """
    Convert each PDF page to an image and OCR it via pytesseract.
    Used as fallback for scanned/image-only PDFs.
    """
    try:
        import pypdf  # type: ignore
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []

        for page_num, page in enumerate(reader.pages):
            # Attempt to extract inline images from the page resources
            resources = page.get("/Resources")
            if resources and resources.get("/XObject"):
                xobjects = resources["/XObject"].get_object()
                for _, obj_ref in xobjects.items():
                    obj = obj_ref.get_object()
                    if obj.get("/Subtype") == "/Image":
                        try:
                            img_data = obj.get_data()
                            width = obj["/Width"]
                            height = obj["/Height"]
                            img = Image.frombytes("RGB", (width, height), img_data)
                            parts.append(pytesseract.image_to_string(img, lang="eng"))
                        except Exception:
                            pass

        return "\n".join(parts).strip()

    except Exception as exc:
        logger.warning("PDF-as-images OCR failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# LLM extraction prompt builder
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
You are a clinical document parser specializing in Indian health insurance discharge summaries and hospital bills.
Extract structured data from the provided medical document text.

RESPONSE FORMAT CONTRACT:
Return ONLY a single raw JSON object — no markdown, no explanation, no preamble.
All monetary values are in INR (Indian Rupees). Use null for any field not found in the document.

Required schema:
{
  "discharge_summary": "full discharge summary text (2-5 sentences max)",
  "condition_diagnosed": "primary diagnosis / ICD description",
  "admission_date": "ISO 8601 date-time string or null",
  "discharge_date": "ISO 8601 date-time string or null",
  "hospitalization_hours": numeric hours or null,
  "claimed_amount": total bill amount as float or null,
  "room_charges": float or null,
  "nursing_charges": float or null,
  "medical_practitioner_fees": float or null,
  "ot_charges": float or null
}
"""


def build_extraction_prompt(raw_text: str) -> tuple[str, str]:
    """
    Returns (system_prompt, user_prompt) for the LLM extraction call.
    Truncates raw_text to _MAX_CHARS.
    """
    truncated = raw_text[:_MAX_CHARS]
    if len(raw_text) > _MAX_CHARS:
        truncated += "\n[... document truncated for context window ...]"

    user_prompt = (
        "Extract structured data from the following medical document text:\n\n"
        f"--- DOCUMENT START ---\n{truncated}\n--- DOCUMENT END ---"
    )
    return _EXTRACTION_SYSTEM_PROMPT, user_prompt
