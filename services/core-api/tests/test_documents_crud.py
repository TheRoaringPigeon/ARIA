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


def _upload(client, entity_ids, *, filename="manual.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf", document_type="manual"):
    data = {"document_type": document_type, "entity_ids": entity_ids}
    return client.post("/documents", files={"file": (filename, content, content_type)}, data=data)


def test_upload_rejects_bad_mime_type(client):
    entity_id = _create_entity(client)
    resp = _upload(client, [entity_id], filename="notes.txt", content=b"hello", content_type="text/plain")
    assert resp.status_code == 400


def test_upload_rejects_empty_entity_ids(client):
    resp = client.post(
        "/documents",
        files={"file": ("manual.pdf", b"%PDF-1.4", "application/pdf")},
        data={"document_type": "manual"},
    )
    assert resp.status_code in (400, 422)


def test_upload_rejects_archived_entity(client):
    entity_id = _create_entity(client)
    assert client.post(f"/entities/{entity_id}/archive").status_code == 200

    resp = _upload(client, [entity_id])
    assert resp.status_code == 400


def test_upload_rejects_missing_entity(client):
    resp = _upload(client, ["does-not-exist"])
    assert resp.status_code == 404


def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 4)
    entity_id = _create_entity(client)
    resp = _upload(client, [entity_id], content=b"way too big for the limit")
    assert resp.status_code == 400


def test_upload_creates_pending_document(client):
    entity_id = _create_entity(client)
    resp = _upload(client, [entity_id])
    assert resp.status_code == 201
    body = resp.json()
    assert body["processing_status"] == "pending"
    assert body["entity_ids"] == [entity_id]
    assert body["document_type"] == "manual"
    assert body["original_filename"] == "manual.pdf"


def test_list_entity_documents_scoped_to_entity(client):
    entity_a = _create_entity(client)
    entity_b = _create_entity(client)

    _upload(client, [entity_a], filename="a.pdf")
    _upload(client, [entity_b], filename="b.pdf")

    docs_a = client.get(f"/entities/{entity_a}/documents").json()
    docs_b = client.get(f"/entities/{entity_b}/documents").json()

    assert [d["original_filename"] for d in docs_a] == ["a.pdf"]
    assert [d["original_filename"] for d in docs_b] == ["b.pdf"]


def test_download_round_trips_bytes(client):
    entity_id = _create_entity(client)
    content = b"%PDF-1.4 some fake pdf bytes for round trip"
    document_id = _upload(client, [entity_id], content=content).json()["id"]

    resp = client.get(f"/documents/{document_id}/file")
    assert resp.status_code == 200
    assert resp.content == content
    assert "manual.pdf" in resp.headers["content-disposition"]


def test_get_nonexistent_document_404(client):
    resp = client.get("/documents/does-not-exist")
    assert resp.status_code == 404


def test_delete_removes_mongo_doc_and_enqueues_storage_cleanup(client, celery_calls):
    entity_id = _create_entity(client)
    upload_resp = _upload(client, [entity_id]).json()
    document_id, storage_path = upload_resp["id"], upload_resp["storage_path"]

    resp = client.delete(f"/documents/{document_id}")
    assert resp.status_code == 204

    assert client.get(f"/documents/{document_id}").status_code == 404
    assert client.get(f"/entities/{entity_id}/documents").json() == []
    assert client.delete(f"/documents/{document_id}").status_code == 404

    # The Mongo row is gone synchronously (asserted above); S3/Chroma
    # cleanup is handed off to the worker task rather than done inline.
    assert ("app.tasks.delete_document.delete_document", [document_id, storage_path]) in celery_calls
