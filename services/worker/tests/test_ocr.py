from io import BytesIO

from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.logic import ocr as ocr_module


def _image(tag: str) -> Image.Image:
    img = Image.new("RGB", (10, 10), color="white")
    img.tag = tag  # identity marker so fakes can key text off which image was passed
    return img


def _blank_page_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _patch_pdf_pipeline(monkeypatch, images):
    monkeypatch.setattr(ocr_module, "convert_from_bytes", lambda file_bytes: images)
    monkeypatch.setattr(
        ocr_module.pytesseract, "image_to_string", lambda img: f"text for {img.tag}"
    )
    calls = []

    def fake_image_to_pdf_or_hocr(img, extension):
        calls.append(img.tag)
        return _blank_page_pdf_bytes()

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_pdf_or_hocr", fake_image_to_pdf_or_hocr)
    return calls


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="JPEG")
    return buf.getvalue()


def test_non_pdf_ignores_make_searchable(monkeypatch):
    monkeypatch.setattr(
        ocr_module.pytesseract, "image_to_string", lambda img: "single image text"
    )

    result = ocr_module.extract_pages(_jpeg_bytes(), "image/jpeg", make_searchable=True)

    assert result.page_texts == ["single image text"]
    assert result.searchable_pdf is None


def test_make_searchable_false_matches_current_behavior(monkeypatch):
    images = [_image("p1"), _image("p2")]
    _patch_pdf_pipeline(monkeypatch, images)

    result = ocr_module.extract_pages(b"pdf-bytes", "application/pdf")

    assert result.page_texts == ["text for p1", "text for p2"]
    assert result.searchable_pdf is None


def test_make_searchable_true_page_texts_identical_to_false(monkeypatch):
    images = [_image("p1"), _image("p2"), _image("p3")]
    _patch_pdf_pipeline(monkeypatch, images)

    without = ocr_module.extract_pages(b"pdf-bytes", "application/pdf", make_searchable=False)
    with_ = ocr_module.extract_pages(b"pdf-bytes", "application/pdf", make_searchable=True)

    # Same extraction call in both cases — never derived from the
    # searchable PDF, so mobile-scan documents can't silently get
    # different chunking/embedding text than any other document source.
    assert with_.page_texts == without.page_texts == ["text for p1", "text for p2", "text for p3"]


def test_make_searchable_true_returns_readable_pdf_with_matching_page_count(monkeypatch):
    images = [_image("p1"), _image("p2")]
    _patch_pdf_pipeline(monkeypatch, images)

    result = ocr_module.extract_pages(b"pdf-bytes", "application/pdf", make_searchable=True)

    assert result.searchable_pdf is not None
    reader = PdfReader(BytesIO(result.searchable_pdf))
    assert len(reader.pages) == len(images)
    for page in reader.pages:
        page.extract_text()  # must not raise


def test_make_searchable_true_runs_second_ocr_pass_per_page(monkeypatch):
    images = [_image("p1"), _image("p2")]
    calls = _patch_pdf_pipeline(monkeypatch, images)

    ocr_module.extract_pages(b"pdf-bytes", "application/pdf", make_searchable=True)

    assert calls == ["p1", "p2"]
