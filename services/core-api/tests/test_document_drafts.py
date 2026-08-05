from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from PIL import Image

from app.config import settings

VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def _create_entity(client, payload=None) -> str:
    resp = client.post("/entities", json=payload or VEHICLE_PAYLOAD)
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_draft(client, entity_ids, *, document_type="manual", shared_with="household"):
    resp = client.post(
        "/documents/drafts",
        json={"document_type": document_type, "entity_ids": entity_ids, "shared_with": shared_with},
    )
    assert resp.status_code == 201
    return resp.json()


def _jpeg_bytes(size=(20, 10), *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, color="red")
    buf = BytesIO()
    if orientation is not None:
        exif = image.getexif()
        exif[274] = orientation
        image.save(buf, format="JPEG", exif=exif)
    else:
        image.save(buf, format="JPEG")
    return buf.getvalue()


def _upload_page(client, draft_id, *, content=None, content_type="image/jpeg", filename="photo.jpg"):
    content = content if content is not None else _jpeg_bytes()
    return client.post(
        f"/documents/drafts/{draft_id}/pages",
        files={"file": (filename, content, content_type)},
    )


def test_create_draft_rejects_missing_entity(client):
    resp = client.post(
        "/documents/drafts",
        json={"document_type": "manual", "entity_ids": ["does-not-exist"]},
    )
    assert resp.status_code == 404


def test_create_draft_rejects_archived_entity(client):
    entity_id = _create_entity(client)
    assert client.post(f"/entities/{entity_id}/archive").status_code == 200
    resp = client.post(
        "/documents/drafts", json={"document_type": "manual", "entity_ids": [entity_id]}
    )
    assert resp.status_code == 400


def test_create_draft_returns_capturing_draft(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    assert draft["status"] == "capturing"
    assert draft["pages"] == []
    assert draft["entity_ids"] == [entity_id]


def test_upload_page_appends_and_bumps_activity(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])

    resp = _upload_page(client, draft["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pages"]) == 1
    assert body["last_activity_at"] >= draft["last_activity_at"]


def test_upload_page_rejects_bad_mime_type(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])

    resp = _upload_page(client, draft["id"], content=b"hello", content_type="text/plain")
    assert resp.status_code == 400

    assert client.get(f"/documents/drafts/{draft['id']}").json()["pages"] == []


def test_upload_page_rejects_oversized_before_push(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 4)
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])

    resp = _upload_page(client, draft["id"])
    assert resp.status_code == 400

    assert client.get(f"/documents/drafts/{draft['id']}").json()["pages"] == []


def test_get_draft_not_found(client):
    assert client.get("/documents/drafts/does-not-exist").status_code == 404


def test_get_draft_page_file_round_trips(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    content = _jpeg_bytes()
    page = _upload_page(client, draft["id"], content=content).json()["pages"][0]

    resp = client.get(f"/documents/drafts/{draft['id']}/pages/{page['id']}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_delete_page_removes_it_and_keeps_remaining_order(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])

    p1 = _upload_page(client, draft["id"]).json()["pages"][-1]
    _upload_page(client, draft["id"])
    p3 = _upload_page(client, draft["id"]).json()["pages"][-1]

    resp = client.delete(f"/documents/drafts/{draft['id']}/pages/{p1['id']}")
    assert resp.status_code == 200
    remaining_ids = [p["id"] for p in resp.json()["pages"]]
    assert p1["id"] not in remaining_ids
    assert remaining_ids[-1] == p3["id"]

    # The deleted page's file is really gone.
    assert (
        client.get(f"/documents/drafts/{draft['id']}/pages/{p1['id']}/file").status_code
        == 404
    )


def test_reorder_pages_valid_permutation_persists(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    pages = []
    for _ in range(3):
        resp = _upload_page(client, draft["id"])
        pages = resp.json()["pages"]
    ids = [p["id"] for p in pages]
    new_order = [ids[2], ids[0], ids[1]]

    resp = client.patch(
        f"/documents/drafts/{draft['id']}/pages/reorder", json={"page_ids": new_order}
    )
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()["pages"]] == new_order

    # Survives a refetch.
    assert [p["id"] for p in client.get(f"/documents/drafts/{draft['id']}").json()["pages"]] == new_order


def test_reorder_pages_mismatched_set_returns_409_without_mutating(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    for _ in range(2):
        resp = _upload_page(client, draft["id"])
    original_order = [p["id"] for p in resp.json()["pages"]]

    resp = client.patch(
        f"/documents/drafts/{draft['id']}/pages/reorder",
        json={"page_ids": [original_order[0], "some-other-id"]},
    )
    assert resp.status_code == 409

    assert [p["id"] for p in client.get(f"/documents/drafts/{draft['id']}").json()["pages"]] == original_order


def test_cancel_draft_deletes_pages_and_row(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    page = _upload_page(client, draft["id"]).json()["pages"][0]

    resp = client.delete(f"/documents/drafts/{draft['id']}")
    assert resp.status_code == 204

    assert client.get(f"/documents/drafts/{draft['id']}").status_code == 404
    assert (
        client.get(f"/documents/drafts/{draft['id']}/pages/{page['id']}/file").status_code
        == 404
    )


def test_finalize_transitions_to_finalizing_and_enqueues(client, celery_calls):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    _upload_page(client, draft["id"])

    resp = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert resp.status_code == 202
    assert resp.json()["status"] == "finalizing"
    assert (
        "app.tasks.finalize_document_draft.finalize_document_draft",
        [draft["id"]],
    ) in celery_calls


def test_finalize_rejects_empty_draft(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])

    resp = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert resp.status_code == 404


def test_finalize_double_submit_returns_409(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    _upload_page(client, draft["id"])

    first = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert first.status_code == 202
    second = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert second.status_code == 409


def test_finalize_enqueue_failure_rolls_back_and_returns_502(client, monkeypatch):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    _upload_page(client, draft["id"])

    def _raise(draft_id):
        raise RuntimeError("redis unreachable")

    import app.routers.documents as documents_module

    monkeypatch.setattr(documents_module, "enqueue_finalize_document_draft", _raise)

    resp = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert resp.status_code == 502

    assert client.get(f"/documents/drafts/{draft['id']}").json()["status"] == "capturing"

    # A retry after the rollback should be allowed to proceed normally.
    monkeypatch.setattr(
        documents_module, "enqueue_finalize_document_draft", lambda draft_id: None
    )
    retry = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert retry.status_code == 202


def test_orientation_normalization_on_page_upload(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])
    content = _jpeg_bytes(size=(20, 10), orientation=6)

    resp = _upload_page(client, draft["id"], content=content)
    page = resp.json()["pages"][0]

    file_resp = client.get(f"/documents/drafts/{draft['id']}/pages/{page['id']}/file")
    result = Image.open(BytesIO(file_resp.content))
    # Orientation 6 is a 90-degree rotation — physical width/height swap.
    assert result.size == (10, 20)
    assert 274 not in result.getexif()


def test_orientation_normalization_on_upload_document_noop_for_pdf(client):
    entity_id = _create_entity(client)
    content = b"%PDF-1.4 fake pdf bytes"
    resp = client.post(
        "/documents",
        files={"file": ("manual.pdf", content, "application/pdf")},
        data={"document_type": "manual", "entity_ids": [entity_id]},
    )
    assert resp.status_code == 201
    document_id = resp.json()["id"]
    assert client.get(f"/documents/{document_id}/file").content == content


def test_concurrent_page_uploads_produce_no_duplicates_or_gaps(client):
    entity_id = _create_entity(client)
    draft = _create_draft(client, [entity_id])

    def _do_upload(_):
        return _upload_page(client, draft["id"])

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(_do_upload, range(5)))

    assert all(r.status_code == 200 for r in responses)
    final_pages = client.get(f"/documents/drafts/{draft['id']}").json()["pages"]
    assert len(final_pages) == 5
    assert len({p["id"] for p in final_pages}) == 5
