# Mobile multi-photo document capture

## Context

ARIA already has a full document pipeline: `POST /documents` (core-api) takes
one PDF/JPEG/PNG, stores it in S3/MinIO, and enqueues an async worker task
(`process_document`) that OCRs it with Tesseract, chunks, and embeds it for
RAG search. Desktop users can already cover "scan to PDF, then upload" with
the existing single-file flow (`DocumentUploadForm.tsx`) — no frontend
changes needed there, though `upload_document` itself gets one small backend
fix shared with this plan's new endpoint (see "Image orientation
normalization" below).

What's missing is a good mobile flow: taking a *series* of phone photos (e.g.
every page of an appliance manual) and ending up with one multi-page document.
Per user decisions (see below), photos must survive a killed/refreshed
mobile tab mid-shoot, which means each photo is uploaded to the server the
moment it's taken, not held only in browser memory.

Decisions made with the user:
- **Capture**: native camera app per shot (`<input type="file" accept="image/*" capture="environment">`), not an in-page `getUserMedia` stream. Simpler, more reliable across iOS/Android.
- **PDF assembly**: server-side (Pillow), not client-side pdf-lib.
- **OCR**: only once, on the finished combined PDF, via the existing worker pipeline — no live per-photo OCR.
- **Resilience**: each captured photo is uploaded to the server immediately and kept there (not just client-side) until the user hits Save or Cancel; a page refresh must be able to resume the in-progress capture.

That last point means we need a small **upload draft** concept: a server-side
staging area for photos captured but not yet stitched into a real `Document`.

## Design

### New Mongo collection: `document_drafts`

New model in `libs/shared/src/aria_shared/models/documents.py` (next to
`Document`):

```python
class DocumentDraftPage(BaseModel):
    id: PyObjectId
    storage_path: str

class DocumentDraft(MongoBaseModel):
    id: PyObjectId = Field(alias="_id")
    household_id: PyObjectId
    entity_ids: list[PyObjectId]
    document_type: DocumentType
    shared_with: SharedWith
    created_by: PyObjectId
    created_at: datetime
    last_activity_at: datetime
    pages: list[DocumentDraftPage] = []
    # capturing -> finalizing -> finalized (or -> failed, retriable back to
    # finalizing). See "finalize" endpoint below for why this exists: PDF
    # assembly runs in the worker, not inline in the request.
    status: Literal["capturing", "finalizing", "finalized", "failed"] = "capturing"
    resulting_document_id: PyObjectId | None = None
    finalize_error: str | None = None
```

No explicit `order` field on a page — a page's position is just its index in
`pages`. Appends use `$push` and deletes use `$pull`, both atomic
document-level Mongo operations, so page order can never be corrupted by
concurrent/retried requests (a count-then-insert approach would be: two
racing page-uploads could both read the same "current count" and write
duplicate/colliding positions). `$pull` also splices out the matched
element and closes the gap, so a retake keeps the remaining pages
correctly ordered with no renumbering step needed.

Append/delete alone aren't enough for real capture sessions, though: a
retake (after a failed shot, or just an out-of-order photo) needs to land
back at a specific position, not just at the end. A dedicated reorder
endpoint (see below) handles that by rewriting the whole `pages` array
atomically, rather than trying to support arbitrary positional inserts on
the append/delete ops themselves.

`last_activity_at` (distinct from `created_at`) is bumped on every mutation
— create, page-upload, delete, reorder — and is what the abandoned-draft
sweep keys off (see "Abandoned-draft cleanup" below), so a draft in active
use, even spread across several days, never expires mid-capture.

Draft photos live at `{household_id}/drafts/{draft_id}/{page_id}.jpg` in the
same `aria-documents` bucket (mirrors the existing
`{household_id}/{document_id}/{filename}` convention in
`services/core-api/app/routers/documents.py:118`).

### New core-api endpoints (`app/routers/documents.py`)

Reuse the existing entity-access/permission-check block from `upload_document`
(lines 90–116) by factoring it into a shared helper (`_check_entity_access`)
called from both the old single-file endpoint and the new draft-create
endpoint — the validation is identical, only the DTO differs.

### Image orientation normalization

Phone cameras commonly store rotation as an EXIF `Orientation` tag rather
than physically rotating pixels. Nothing in the current pipeline accounts
for this — not `upload_document`, not `ocr.py`'s single-image branch — so
a portrait shot uploaded as-is stays sideways through storage, thumbnails,
and OCR (Tesseract's accuracy falls off sharply on rotated text). This
plan makes it common (mobile camera capture is the whole point), so it
gets fixed at the source rather than patched around later.

New helper in `app/routers/documents.py`, shared by both `upload_document`
and the new page-upload endpoint below:

```python
def _normalize_image_orientation(content: bytes, mime_type: str) -> bytes:
    """Re-encodes with EXIF rotation baked into the pixels, quality=90.
    No-op for PDFs (no EXIF-rotation concept at this layer) and for images
    with no orientation tag. Dropping the original EXIF blob on re-encode
    also strips any embedded GPS/location data before the photo lands in a
    shared household document."""
    if mime_type not in ("image/jpeg", "image/png"):
        return content
    image = ImageOps.exif_transpose(Image.open(BytesIO(content)))
    buf = BytesIO()
    image.save(buf, format="JPEG" if mime_type == "image/jpeg" else "PNG", quality=90)
    return buf.getvalue()
```

Run through `run_in_threadpool` at both call sites (CPU-bound decode/re-encode), same as the existing `s3.upload` calls in this router. Normalizing once here — rather than at thumbnail-serving or PDF-assembly time — means everything downstream (`draftPageUrl` thumbnails, the worker's PDF assembly, `download_document`) just reads already-correct bytes with no special-casing.

- `upload_document` (existing endpoint, one-line change): call `_normalize_image_orientation(content, file.content_type)` right after `content = await file.read()`, before the `max_upload_bytes` check — so the size check and the stored `file_size_bytes` both reflect the re-encoded bytes, not the raw upload. PDF uploads pass through untouched. Frontend (`DocumentUploadForm.tsx`) is unchanged.

- `POST /documents/drafts` — body: `document_type`, `entity_ids`, `shared_with` (JSON, no file). Runs the same entity/permission checks as today's upload, creates a `DocumentDraft` row (`last_activity_at = created_at`), returns its id. Called once when the capture modal opens.
- `POST /documents/drafts/{draft_id}/pages` — multipart, one image file. Validates mime type (`image/jpeg`/`image/png` only — no PDF here), enforces `settings.max_upload_bytes` per-photo (same check `upload_document` does today — reject immediately rather than only at finalize), runs the content through `_normalize_image_orientation`, uploads the normalized bytes to S3 under the draft's prefix, `$push`es a `DocumentDraftPage` onto the draft's `pages` array, bumps `last_activity_at`, returns the updated draft. Called immediately after every camera shot.
- `GET /documents/drafts/{draft_id}` — returns the draft including its pages, so the modal can reload state after a refresh (client persists `draft_id` in `localStorage`, checks for it on mount, and calls this to rebuild the thumbnail strip).
- `GET /documents/drafts/{draft_id}/pages/{page_id}/file` — streams a single page image, same shape as the existing `download_document` (documents.py:204-217), used for thumbnails after a reload.
- `DELETE /documents/drafts/{draft_id}/pages/{page_id}` — deletes one page's S3 object and `$pull`s it from the draft's `pages` array (retake/remove a shot); remaining pages keep their relative order automatically. Bumps `last_activity_at`.
- `PATCH /documents/drafts/{draft_id}/pages/reorder` — body: `page_ids`, the full list of the draft's page ids in the desired order. Validates that the set of ids is exactly a permutation of the draft's current `pages` ids — a mismatch (page added/deleted concurrently, e.g. from another tab) returns `409` so the client can refetch and retry rather than silently applying a stale order. On success, atomically `$set`s `pages` to the reordered list and bumps `last_activity_at`. Used both for fixing an out-of-order shot and for repositioning a retake that landed at the end of the stack.
- `POST /documents/drafts/{draft_id}/finalize` — the "Save"/"Create" action. This is **asynchronous**: assembling a PDF from several full-resolution phone photos means multiple S3 downloads plus decoding/encoding real image data, and doing that inline on the request thread would block the core-api event loop and hold an HTTP connection open over a mobile network for the whole assembly — exactly the kind of work this codebase otherwise always hands to the worker (see `process_document`). The endpoint itself just kicks the work off:
  1. Load the draft, 404 if missing/empty.
  2. Atomically flip `status` from `capturing`/`failed` to `finalizing` via `find_one_and_update` guarded on the current status. A no-match means a finalize is already in flight or already succeeded — return 409 rather than double-enqueueing (guards against a double-tap on "Create" or a client retry racing the first request).
  3. `send_task` the new worker task below via a new `enqueue_finalize_document_draft` in `celery_client.py`. Unlike `enqueue_document_processing`/`enqueue_document_deletion`, **this enqueue is not fire-and-forget**: those two are allowed to degrade silently because the `Document` row they act on already exists either way (CRUD still works; only the ingestion pipeline is delayed). Here there's no `Document` yet — the enqueue *is* the operation. If `send_task` raises (Redis unreachable), roll the draft's status back to `capturing` and return `502` so the client can retry, instead of logging-and-swallowing.
  4. Return `202` with the updated draft (`status: "finalizing"`).

  New worker task `services/worker/app/tasks/finalize_document_draft.py`, structured like `purge_expired_trash.py` (plain pymongo, re-checks state rather than trusting the snapshot that enqueued it):
  1. Re-load the draft; bail if missing or not `finalizing` (defensive — shouldn't happen in practice).
  2. Re-run `_check_entity_access` against the draft's `entity_ids` (same helper `upload_document`/draft-create use). The linked entity can be archived, unshared, or trashed-and-purged by another household member during the time a draft sits in `capturing` — this is the last point before a `Document` is actually created, so it's checked again here rather than trusting the check from draft-creation time. On failure: set `status="failed"`, `finalize_error="entity no longer accessible"`, leave the draft and its pages in place, and stop.
  3. Download each page from S3 (in `pages` array order), combine into a single PDF in-memory with Pillow (`Image.save(buf, format="PDF", save_all=True, append_images=[...])`) — no new heavy dependency; Pillow is a normal image library, not an "AI dependency" per this service's stated scope. No orientation handling needed here — pages were already normalized by `_normalize_image_orientation` at upload time.
  4. Enforce `settings.max_upload_bytes` against the combined PDF size, same check as today's single-file path. On failure: set `status="failed"`, `finalize_error=<message>`, leave the draft and its pages in place (so the client can show the error and offer Retry — re-entering step 2 from `failed` — or Cancel), and stop.
  5. Run through the *existing* document-creation code (extract the tail of `upload_document` — S3 store, `Document(...)`, `db.documents.insert_one`, `enqueue_document_processing`) into a shared helper so this path and the classic single-file path both call it.
  6. Delete the draft's page objects from S3.
  7. Set `status="finalized"`, `resulting_document_id=<new Document id>`. Leave the (now page-less) draft row itself — the client deletes it once it's read the result (see Frontend below); the existing `purge_expired_upload_drafts` sweep is a backstop if it never does.
- `DELETE /documents/drafts/{draft_id}` — Cancel: deletes all page S3 objects and the draft row.

### Abandoned-draft cleanup (worker)

Mirrors the existing grace-period-trash pattern exactly
(`services/worker/app/tasks/purge_expired_trash.py`,
`services/worker/app/celery_app.py:26-38`):

- New setting `upload_draft_ttl_hours = 168` (7 days, matching the session-cookie TTL convention used elsewhere in this codebase) in both `core-api/app/config.py` and `worker/app/config.py` (same duplication pattern already used for `entity_trash_grace_hours`). A fixed TTL from `created_at` would still expire a draft mid-capture for anyone working across more than a week in short bursts, so the sweep keys off `last_activity_at` instead (bumped on every create/page-upload/delete/reorder) — a draft is only purged after `upload_draft_ttl_hours` of genuine inactivity, not wall-clock time since it was started.
- New task `services/worker/app/tasks/purge_expired_upload_drafts.py`: finds `document_drafts` with `last_activity_at` older than the TTL, deletes each page's S3 object plus the draft row.
- New hourly `beat_schedule` entry alongside `purge-expired-trash-hourly`.

### Frontend

- `services/frontend/src/api/documentDrafts.ts` (new, mirrors `api/documents.ts`'s raw-`fetch`/`FormData` style since these are also multipart/JSON-mixed calls): `createDraft`, `uploadDraftPage`, `getDraft`, `deleteDraftPage`, `reorderDraftPages`, `finalizeDraft`, `cancelDraft`, `draftPageUrl`. `finalizeDraft` now returns the `finalizing`-status draft, not a `Document` — there's no more synchronous 201 to react to.
- `services/frontend/src/components/PhotoCaptureModal.tsx` (new), styled like `ExportPdfModal.tsx`'s modal shell (`fixed inset-0 bg-black/40`, Escape-to-close, click-outside-to-close):
  - On mount: read `documentDraftId` from `localStorage`. If present, `getDraft` to resume (rebuild thumbnail strip from existing pages); if the draft 404s (already finalized/expired), clear the stored id and start fresh. If it comes back with `status === "finalizing"`, skip straight to the finalizing view below and resume polling — a refresh mid-finalize shouldn't drop the user back into a capture UI for a draft that's already being consumed.
  - If no draft yet, don't create one until the document-type/sharing fields are chosen (reuse `SharingControl.tsx` and the same `DOCUMENT_TYPES` list as `DocumentUploadForm.tsx`) and the user taps "Start" — then `createDraft`, store the id in `localStorage`.
  - "Take photo" button is a hidden `<input type="file" accept="image/*" capture="environment">`. Each shot is a **blocking step**, not fire-and-forget: on change, the captured blob is held in component state and a new thumbnail is added immediately in an "uploading" state (spinner overlay) while `uploadDraftPage` is in flight; the shutter button is disabled for the duration. On success, the thumbnail settles to normal and the shutter re-enables for the next shot. On failure, the thumbnail shows an error state with **Retry** (re-POSTs the same held blob — no re-shoot needed) and **Discard** (drops the shot); the shutter stays disabled until one of those is chosen. This is what actually prevents a missed/failed page from getting silently skipped: the user physically cannot advance to the next page with an unresolved failure in the stack.
  - Thumbnail strip below shows each uploaded page (`draftPageUrl`) with a delete button (`deleteDraftPage`). Tapping a thumbnail selects it (highlighted border) and reveals move-left/move-right arrow buttons (disabled at the respective end of the strip); each tap recomputes the new page-id order client-side and persists it immediately via `reorderDraftPages`, same immediate-persist pattern as delete — used both to fix an out-of-order shot and to reposition a retake (which lands at the end of the stack after a Discard-and-reshoot) back into its correct spot. A `409` from `reorderDraftPages` (concurrent edit from another tab) triggers a `getDraft` refetch and drops the pending reorder, rather than applying a stale order.
  - Footer: "Cancel" (`cancelDraft`, clear `localStorage`, close) and "Create" (`finalizeDraft`).
  - "Create" transitions the modal into a finalizing view: while `status === "finalizing"`, show a "Creating…" state and poll `getDraft` (TanStack Query `refetchInterval`, ~1.5s) instead of closing. On `status === "finalized"`: read `resulting_document_id`, invalidate `['documents', entityId]`, call `deleteDraft` to clean up the now-empty draft row, clear `localStorage`, close. On `status === "failed"`: show `finalize_error` inline with Retry (calls `finalizeDraft` again) and Cancel actions. On a `502` from the initial `finalizeDraft` call itself (enqueue failed outright), show an inline error and let the user tap "Create" again — the draft is still `capturing` server-side.
- `useDocumentDraft.ts` (new hook) wraps the above with TanStack Query mutations, following `useUploadDocument.ts`'s shape.
- Wire-up point: `EntityDetailPage.tsx:520-548`, next to the existing "Upload document" button — add a second "Take photos" button that opens `PhotoCaptureModal` instead of toggling `DocumentUploadForm`. Keep the existing single-file form as-is for desktop/scan-to-PDF use.

### Out of scope (explicitly, per user decisions above)

- No `getUserMedia` in-page camera stream.
- No client-side PDF assembly library.
- No live per-photo OCR — the finished PDF goes through the exact same `process_document` worker task unchanged.
- No client-side image downscaling/compression in this first pass — if upload size becomes a real problem in practice, revisit later rather than building it speculatively now.

## Verification

- New/updated pytest coverage in `services/core-api/tests/` for: draft create/page-upload/get/delete-page/cancel, including permission and shared-household checks reusing the same fixtures as `test_documents_*` (check existing test file naming there first). For `finalize`, cover: the `capturing`→`finalizing` transition and its 409 on double-submit, the rollback-to-`capturing`+502 path when `send_task` raises (mock the celery client), and the entity-access re-check (archive/unshare the linked entity between draft-creation and finalize, confirm `status="failed"` with `finalize_error` set rather than a `Document` being created against a now-inaccessible entity).
- New pytest coverage for the per-page size check on `POST /documents/drafts/{draft_id}/pages`: an oversized image is rejected immediately (before S3 upload, before `$push`), mirroring the existing `upload_document` oversize test.
- New pytest coverage for `_normalize_image_orientation`: a JPEG fixture with a non-1 `Orientation` EXIF tag comes out with pixels physically rotated and no orientation tag on the result; a PDF passed through comes back byte-identical (no-op). Cover both call sites (`upload_document`, page-upload) confirming `file_size_bytes`/the stored S3 object reflect the normalized bytes, not the raw upload.
- New pytest coverage firing concurrent/interleaved page-upload requests at the same draft (e.g. `asyncio.gather` over several `uploadDraftPage`-equivalent calls in a test) and asserting the resulting `pages` array has exactly one entry per request, in a stable order with no duplicates/gaps — regression test for the count-then-insert race this `$push`-based design replaces.
- New pytest coverage for `PATCH /documents/drafts/{draft_id}/pages/reorder`: a valid permutation of the current page ids persists in the new order and bumps `last_activity_at`; a `page_ids` list that doesn't match the current set (missing id, extra id, or one from a concurrent add/delete) returns 409 without mutating `pages`.
- New pytest coverage in `services/worker/tests/` for `finalize_document_draft`: success path (assembled PDF, `Document` created, pages deleted from S3, draft marked `finalized` with `resulting_document_id` set), and the oversize-PDF path (draft marked `failed` with `finalize_error`, pages and draft left in place, then a retry succeeding from `failed`).
- New pytest coverage in `services/worker/tests/` for `purge_expired_upload_drafts`, modeled on `test_purge_expired_trash.py` if it exists (check): a draft with a stale `last_activity_at` is purged; a draft with an old `created_at` but recent `last_activity_at` (e.g. resumed after several days of inactivity, then actively edited) is left alone — regression test for the fixed-TTL-from-creation approach this `last_activity_at`-based design replaces.
- Manual end-to-end: use the `verify` skill to bring the stack up via docker-compose, then from a phone (or Chrome device-emulation with a fake camera) walk through: open entity → Take photos → capture 3+ images, including at least one shot held in portrait orientation → refresh the page mid-shoot and confirm the draft resumes with existing thumbnails intact and right-side up → delete one page → Create → confirm the modal shows "Creating…" and polls rather than closing immediately → confirm a single multi-page `Document` appears with `processing_status` progressing `pending → ocr_complete → chunked → embedded`, and that `GET /documents/{id}/file` downloads a valid multi-page PDF with every page correctly oriented.
- Manual: stop the `redis` container right before tapping "Create" and confirm the client shows a retryable error rather than a silently stuck spinner; restart redis and confirm Retry succeeds.
- Manual: temporarily lower `max_upload_bytes` so finalize hits the oversize path, confirm the modal surfaces `finalize_error` with working Retry/Cancel actions, then restore the setting and confirm Retry succeeds.
- Manual: simulate a failed page-upload (e.g. throttle/kill network mid-shot via devtools) and confirm the thumbnail shows an error state with working Retry (succeeds once network is restored) and Discard, and that the shutter is genuinely disabled — can't advance to the next photo — while the failure is unresolved.
- Manual: capture 4+ pages, select a thumbnail, and confirm the move-left/move-right arrows correctly reorder the strip and persist (refresh the page and confirm the new order survives); also confirm a Discard-and-reshoot lands at the end of the strip and can be moved back into position with the same arrows.
- Confirm `purge_expired_upload_drafts` actually deletes a manually-backdated draft's S3 objects and Mongo row (unit test plus a quick manual check via the Mongo shell / MinIO console in the dev stack), using a backdated `last_activity_at` rather than `created_at`.
