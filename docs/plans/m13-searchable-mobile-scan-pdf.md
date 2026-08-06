# Searchable text layer for mobile-scan PDFs

## Context

[m12](m12-mobile-photo-capture.md) added the "Take photos" flow: a series of
phone shots gets stitched into one PDF by
`services/worker/app/tasks/finalize_document_draft.py` via
`Image.save(format="PDF", save_all=True, append_images=[...])`. That call
only concatenates page images — the resulting `mobile-scan.pdf` has no text
layer at all, so `Ctrl+F` finds nothing when a user downloads it from MinIO,
even though the very same photos get OCR'd immediately afterward by the
existing `process_document` worker task
(`services/worker/app/tasks/process_document.py`,
`services/worker/app/logic/ocr.py`). That OCR output currently only reaches
a Chroma embedding for semantic search — the plain text itself is never
written anywhere retrievable, and definitely never back into the PDF.

This plan makes `process_document` rewrite a mobile-scan PDF in place with
an invisible, position-matched text layer under each page image — the same
technique `ocrmypdf` and phone scanner apps use — so the file at
`storage_path` becomes natively searchable/selectable, with no change to
`GET /documents/{id}/file` or any frontend code (same object, same path,
just different bytes).

**Scope decision**: this only applies to documents created via the
mobile-scan flow, identified by a new `Document.source` field, not to PDFs
in general. A real/native PDF (or a desktop "scan to PDF, then upload") may
already have its own text layer or real vector text; rasterizing and
OCR-replacing that would be a quality regression, and reliably detecting
"this PDF has no usable text layer" from bytes alone is a heuristic that can
misfire. Scoping to a provenance flag set only by code we control
(`finalize_document_draft.py`) avoids that risk entirely. Desktop
scan-to-PDF uploads getting the same treatment is a plausible future
extension, not part of this plan.

## Design

### `Document.source` field

`libs/shared/src/aria_shared/models/documents.py`:

```python
DocumentSource = Literal["upload", "mobile_scan"]

class Document(MongoBaseModel):
    ...
    source: DocumentSource = "upload"
```

Default `"upload"` means every existing call site (`upload_document` in
`services/core-api/app/routers/documents.py`) needs no change. Existing
Mongo rows predate this field entirely; `process_document.py` reads it with
`doc.get("source")` (not `doc["source"]`), so old documents fall through to
today's unchanged behavior rather than erroring.

`finalize_document_draft.py`'s `Document(...)` construction gets the one
new kwarg: `source="mobile_scan"`.

### `ocr.py`: optional searchable-PDF output

New dependency: `pypdf` (added to `services/worker/pyproject.toml`,
alongside the existing `pytesseract`/`pdf2image`/`pillow` — no new system
package, it's pure Python and reuses the same tesseract binary already
required for OCR).

```python
from dataclasses import dataclass

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
```

`page_texts` feeds `chunk_pages` exactly as `extract_pages`'s old return
value did, and is computed identically regardless of `make_searchable` —
the chunking/embedding stage downstream doesn't change at all, for any
document source.

### `process_document.py`: rewrite storage in place, after the pipeline succeeds

The searchable-PDF rewrite runs *after* `chunk_pages`/embedding complete and
`processing_status` reaches `embedded`, not before, and its failure —
including "too big" — is caught locally and logged rather than allowed to
fail the whole task. OCR/chunk/embed already succeeded by that point, so a
document must not end up `processing_status="failed"` (losing semantic
search entirely) just because the bonus searchable-PDF rewrite didn't fit
under `max_upload_bytes` or hit some other error.

It's also guarded against the document having been deleted while
`process_document` was still running (mobile-scan OCR now costs a second
tesseract pass per page — see the `ocr.py` section — so this task runs
longer, widening the window), the same way the existing Chroma write a few
lines up already is (`process_document.py:55-61` in the current code: "The
entity-delete cascade can enqueue delete_document for this same id while
this task is still mid-pipeline"). `DELETE /documents/{id}` deletes the
Mongo row synchronously with no check on `processing_status`
(`documents.py:254-280`), so this isn't a hypothetical race. Without the
guard, `delete_document`'s `s3.delete(storage_path)` could run first,
followed by this step's `s3.upload(storage_path, ...)` recreating an S3
object with no Mongo row pointing at it — a permanent leak, since nothing
else purges orphaned S3 keys.

```python
import logging

from app.config import settings

logger = logging.getLogger(__name__)

# ... inside process_document(), after `_set_status(db, document_id, "embedded")`:

if result.searchable_pdf is not None:
    _write_searchable_pdf(db, document_id, doc["storage_path"], result.searchable_pdf)


def _write_searchable_pdf(db, document_id: str, storage_path: str, searchable_pdf: bytes) -> None:
    try:
        if len(searchable_pdf) > settings.max_upload_bytes:
            raise ValueError(
                f"searchable PDF exceeds maximum upload size of {settings.max_upload_bytes} bytes"
            )
        if db.documents.find_one({"_id": document_id}) is None:
            return
        s3.upload(storage_path, BytesIO(searchable_pdf), "application/pdf")
        db.documents.update_one(
            {"_id": document_id}, {"$set": {"file_size_bytes": len(searchable_pdf)}}
        )
    except Exception:
        logger.exception("failed to write searchable PDF for document %s", document_id)
```

The size check reuses the same `settings.max_upload_bytes` ceiling
`finalize_document_draft.py` already enforces on the image-only assembly.
Because `image_to_pdf_or_hocr` re-renders each page's image rather than
reusing the JPEG bytes `finalize_document_draft.py` originally wrote, the
searchable rewrite is often *larger* than the original assembled PDF — a
scan that just barely passed `finalize_document_draft`'s size check can
still trip this one. On any failure inside `_write_searchable_pdf`
(oversize, the deleted-row race, or anything else), the document simply
keeps `processing_status="embedded"` and the original (non-searchable) PDF
stays at `storage_path` — full semantic search still works, the file is
just not text-searchable in a PDF viewer, matching pre-this-plan behavior
for that one file.

Overwriting the same `storage_path` (not a new key) is what makes this a
zero-blast-radius change outside the worker: `GET
/documents/{id}/file` streams from `storage_path` unconditionally, so
nothing in core-api or the frontend needs to know this happened.

### Idempotency note

If `process_document` ever reruns on a document whose PDF has already been
rewritten (retry, manual re-trigger), `convert_from_bytes` rasterizes the
already-searchable PDF's *visible* image layer (the invisible text layer
doesn't affect rendering), and OCR runs again the same way. Slightly
redundant CPU, not incorrect — no special-casing needed.

### Delete-during-processing note

Separately from reruns: a document can be deleted (`DELETE
/documents/{id}`, or the entity-delete cascade) while `process_document` is
still mid-flight, since neither path checks `processing_status` first. The
existing Chroma write already re-checks the row exists immediately before
writing; `_write_searchable_pdf` above does the same immediately before its
`s3.upload`. Both writes are the only two places this task persists
anything keyed by `document_id` outside its own status field, so those two
checks are the complete guard — no broader locking needed.

## Out of scope

- Desktop "scan to PDF, then upload" documents (`source="upload"`, possibly
  also image-only) — not rewritten, per the Scope decision above.
- Single image uploads (JPEG/PNG, one-page "documents") — no PDF exists to
  add a text layer to; unchanged.
- Any change to the Chroma embedding/chunking pipeline, `chunk_pages`, or
  search itself — `page_texts` is computed identically (always
  `pytesseract.image_to_string`) regardless of `make_searchable`, so
  chunking/embedding quality is unaffected; only where that same text also
  ends up (embedded in the PDF file, not just Chroma) changes.
- Recovering the searchable-PDF rewrite if `_write_searchable_pdf` fails —
  the document stays fully functional (`embedded`, original PDF in place),
  it's just not text-searchable in a PDF viewer until someone manually
  re-triggers processing. No automatic retry of just that step.
- A migration/backfill for PDFs created by the mobile-scan flow *before*
  this plan ships — they stay image-only unless manually re-processed
  (out of scope; can revisit if it turns out to matter in practice).

## Verification

- New unit tests in `services/worker/tests/test_ocr.py` (new file),
  monkeypatching `pytesseract.image_to_pdf_or_hocr` /
  `pytesseract.image_to_string` / `convert_from_bytes` the way other worker
  tests monkeypatch external calls (e.g. `ollama.embed_batch` in the
  chunking tests) rather than depending on a real tesseract binary being on
  the dev/CI machine:
  - `make_searchable=False` (default): `searchable_pdf` is `None`,
    `page_texts` unchanged from today — regression test that non-mobile-scan
    documents keep exactly their current behavior.
  - `make_searchable=True`: `page_texts` is byte-for-byte identical to what
    `make_searchable=False` returns for the same images (proves it's always
    sourced from `image_to_string`, never from the searchable PDF); returns
    a `searchable_pdf` whose page count matches the input image count and
    whose pages are non-empty/pypdf-readable (sanity check on the rewrite
    itself, not a content-equality assertion against `page_texts`).
  - Non-PDF `mime_type` ignores `make_searchable` entirely (single-image
    path never produces a `searchable_pdf`).
- New unit tests in `services/worker/tests/test_process_document.py` (new
  file, doesn't exist yet — check for one first in case this lands after
  another change adds it), monkeypatching `extract_pages`:
  - `source="mobile_scan"` document: `s3.upload` is called with the
    document's existing `storage_path` and the `searchable_pdf` bytes;
    `file_size_bytes` on the Mongo row is updated to match; final
    `processing_status` is `embedded`.
  - `source="upload"` (or missing `source` key, simulating a pre-migration
    row) document: `s3.upload` is never called for the original file at
    all — only the existing OCR→chunk→embed status progression happens.
  - Oversize `searchable_pdf` (mock `settings.max_upload_bytes` down):
    `s3.upload` is never called (original file untouched), but
    `processing_status` still ends at `embedded` and Chroma still received
    the chunk embeddings — the rewrite failure must not regress the
    existing OCR/chunk/embed pipeline. Assert on `caplog`/a mocked `logger`
    that the failure was logged, not swallowed silently.
  - Document row deleted between `extract_pages` returning and the
    searchable-PDF write (simulate by deleting the row, or monkeypatching
    `db.documents.find_one` for that one call, inside the mocked
    `extract_pages`): `s3.upload` is never called — the delete-race guard
    in `_write_searchable_pdf` must fire before the upload, not after.
- Manual end-to-end via the `verify` skill: capture a multi-page mobile scan
  through "Take photos" → Create, wait for `processing_status` to reach
  `embedded`, download the document via `GET /documents/{id}/file`, and
  confirm in a PDF viewer that `Ctrl+F` finds text from the photographed
  pages and that the pages still look/render like photos (image layer
  intact, text layer invisible).
  - Include one scan sized close to `max_upload_bytes` (e.g. several
    high-resolution phone photos) specifically to check the case where the
    searchable rewrite grows past the limit: confirm `processing_status`
    still reaches `embedded`, semantic search over the document still
    works, and the downloaded PDF is the original (non-searchable, but
    intact) file.
- Manual: confirm a regular desktop single-file PDF upload
  (`DocumentUploadForm.tsx`) is byte-for-byte unaffected — same
  `storage_path` object before and after `process_document` runs.
