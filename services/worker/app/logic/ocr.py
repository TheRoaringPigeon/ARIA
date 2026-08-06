from dataclasses import dataclass
from io import BytesIO

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from pypdf import PdfReader, PdfWriter


@dataclass
class OcrResult:
    page_texts: list[str]
    # Set only when `make_searchable=True` produced a rewritten PDF (each
    # page's image plus an invisible OCR text layer) meant to replace the
    # original bytes at storage_path. None for images and for PDFs OCR'd
    # only to get chunking text.
    searchable_pdf: bytes | None = None


def extract_pages(file_bytes: bytes, mime_type: str, make_searchable: bool = False) -> OcrResult:
    """OCR every page of a document into plain text, one string per page.

    PDFs are rasterized page-by-page via pdf2image (poppler); single-image
    uploads (JPEG/PNG) are treated as a one-page document. Both paths feed
    pytesseract directly from in-memory bytes — no temp-file bookkeeping.

    `make_searchable=True` (PDFs only) additionally rewrites the pages into
    a PDF with an invisible, position-matched OCR text layer under each
    page image — the same technique ocrmypdf/scanner apps use.
    """
    if mime_type != "application/pdf":
        images = [Image.open(BytesIO(file_bytes))]
        return OcrResult(page_texts=[pytesseract.image_to_string(img) for img in images])

    images = convert_from_bytes(file_bytes)
    # Always the same extraction call regardless of make_searchable, so
    # chunking/embedding text is identical in shape and quality across every
    # document source. Deliberately NOT derived from the searchable PDF
    # below: pypdf's extract_text() on a tesseract-rendered PDF is not
    # guaranteed to match image_to_string()'s output (whitespace/line-break/
    # reading-order can differ), and mobile-scan documents must not get
    # silently different — likely lower-fidelity — search text than every
    # other document source just because they also get a PDF rewrite.
    page_texts = [pytesseract.image_to_string(img) for img in images]

    if not make_searchable:
        return OcrResult(page_texts=page_texts)

    # image_to_pdf_or_hocr runs its own, separate OCR pass and returns a
    # one-page PDF per image: the original image plus an invisible,
    # position-matched text layer — this is the actual "searchable scan"
    # mechanism (what ocrmypdf/scanner apps produce). This is a second,
    # independent tesseract pass per page (not reused for page_texts above)
    # — paid only by mobile-scan PDFs, in exchange for keeping the
    # search-quality-critical page_texts uniform across all sources.
    page_pdfs = [pytesseract.image_to_pdf_or_hocr(img, extension="pdf") for img in images]

    writer = PdfWriter()
    for page_pdf in page_pdfs:
        writer.append(PdfReader(BytesIO(page_pdf)))
    out = BytesIO()
    writer.write(out)

    return OcrResult(page_texts=page_texts, searchable_pdf=out.getvalue())
