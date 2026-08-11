"""Regression coverage for scaling-debt.md #8
(docs/scaling-debt-plans/sd8-permissions-registry-gaps.md): the five
document-draft mutation routes (upload/delete/reorder page, finalize,
cancel) had no check_permission call at all — only sharing was checked,
unlike every other mutating resource in the app.

(A second gap found in the same pass — delete_document/rename_document
skipping their permission check when a document's entity_ids was empty —
was fixed at the root instead: purge_expired_trash.py now deletes such a
document's row in the same transaction that would otherwise leave it
orphaned, so entity_ids empty is no longer a reachable state for a live
document. See services/worker/tests/test_purge_expired_trash.py for that
coverage.)
"""

from io import BytesIO

from PIL import Image

from tests.conftest import set_session_role

from aria_auth import PERMISSIONS

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


def _create_draft(client, entity_id) -> dict:
    resp = client.post(
        "/documents/drafts",
        json={"document_type": "manual", "entity_ids": [entity_id]},
    )
    assert resp.status_code == 201
    return resp.json()


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (20, 10), color="red")
    buf = BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


def _upload_page(client, draft_id):
    return client.post(
        f"/documents/drafts/{draft_id}/pages",
        files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
    )


# --- Document drafts had no check_permission call at all -------------------


def test_member_can_complete_full_draft_lifecycle_with_unrestricted_registry(client):
    """No PERMISSIONS entry restricts "update" today, so wiring the new
    check into every draft route shouldn't change behavior for the common
    case — a member can still capture, reorder, and finalize normally.
    """
    entity_id = _create_entity(client)
    set_session_role("member")

    draft = _create_draft(client, entity_id)
    upload = _upload_page(client, draft["id"])
    assert upload.status_code == 200

    finalize = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert finalize.status_code == 202


def test_member_can_cancel_own_in_progress_draft_with_unrestricted_registry(client):
    entity_id = _create_entity(client)
    set_session_role("member")
    draft = _create_draft(client, entity_id)

    assert client.delete(f"/documents/drafts/{draft['id']}").status_code == 204


def test_registry_entry_blocks_disallowed_role_on_draft_page_upload(client, monkeypatch):
    monkeypatch.setitem(PERMISSIONS, ("vehicle", "update"), frozenset({"owner"}))
    entity_id = _create_entity(client)
    draft = _create_draft(client, entity_id)

    set_session_role("member")
    resp = _upload_page(client, draft["id"])
    assert resp.status_code == 403


def test_registry_entry_blocks_disallowed_role_on_draft_page_delete(client, monkeypatch):
    entity_id = _create_entity(client)
    draft = _create_draft(client, entity_id)
    page = _upload_page(client, draft["id"]).json()["pages"][0]

    monkeypatch.setitem(PERMISSIONS, ("vehicle", "update"), frozenset({"owner"}))
    set_session_role("member")
    resp = client.delete(f"/documents/drafts/{draft['id']}/pages/{page['id']}")
    assert resp.status_code == 403


def test_registry_entry_blocks_disallowed_role_on_draft_reorder(client, monkeypatch):
    entity_id = _create_entity(client)
    draft = _create_draft(client, entity_id)
    for _ in range(2):
        _upload_page(client, draft["id"])
    pages = client.get(f"/documents/drafts/{draft['id']}").json()["pages"]
    ids = [p["id"] for p in pages]

    monkeypatch.setitem(PERMISSIONS, ("vehicle", "update"), frozenset({"owner"}))
    set_session_role("member")
    resp = client.patch(
        f"/documents/drafts/{draft['id']}/pages/reorder",
        json={"page_ids": list(reversed(ids))},
    )
    assert resp.status_code == 403


def test_registry_entry_blocks_disallowed_role_on_draft_finalize(client, monkeypatch):
    entity_id = _create_entity(client)
    draft = _create_draft(client, entity_id)
    _upload_page(client, draft["id"])

    monkeypatch.setitem(PERMISSIONS, ("vehicle", "update"), frozenset({"owner"}))
    set_session_role("member")
    resp = client.post(f"/documents/drafts/{draft['id']}/finalize")
    assert resp.status_code == 403


def test_registry_entry_blocks_disallowed_role_on_draft_cancel(client, monkeypatch):
    entity_id = _create_entity(client)
    draft = _create_draft(client, entity_id)

    monkeypatch.setitem(PERMISSIONS, ("vehicle", "update"), frozenset({"owner"}))
    set_session_role("member")
    resp = client.delete(f"/documents/drafts/{draft['id']}")
    assert resp.status_code == 403
