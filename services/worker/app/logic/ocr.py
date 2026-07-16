from io import BytesIO

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image


def extract_pages(file_bytes: bytes, mime_type: str) -> list[str]:
    """OCR every page of a document into plain text, one string per page.

    PDFs are rasterized page-by-page via pdf2image (poppler); single-image
    uploads (JPEG/PNG) are treated as a one-page document. Both paths feed
    pytesseract directly from in-memory bytes — no temp-file bookkeeping.
    """
    if mime_type == "application/pdf":
        images = convert_from_bytes(file_bytes)
    else:
        images = [Image.open(BytesIO(file_bytes))]

    return [pytesseract.image_to_string(image) for image in images]
