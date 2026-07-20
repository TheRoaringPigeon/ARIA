from tests.conftest import set_session_role

from aria_auth import PERMISSIONS

VEHICLE_PAYLOAD = {
    "domain": "vehicle",
    "name": "Test Truck",
    "status": "active",
    "attributes": {"domain": "vehicle", "make": "Ford", "model": "Ranger", "year": 2021},
}


def test_member_can_archive_and_restore_with_no_registry_restriction(client):
    """Archive/restore have no PERMISSIONS entry (M9's only real entry is
    hard-delete, see test_permissions_delete.py) — this guards against a
    future registry entry accidentally changing that default.
    """
    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]

    set_session_role("member")
    assert client.post(f"/entities/{entity_id}/archive").status_code == 200
    assert client.post(f"/entities/{entity_id}/restore").status_code == 200


def test_registry_entry_blocks_disallowed_role(client, monkeypatch):
    """Proves the check_permission() seam — now wired in as a Depends() on
    each mutating route — actually enforces once a real policy is added: a
    future restriction is a registry entry, not a router change.
    """
    monkeypatch.setitem(PERMISSIONS, ("vehicle", "archive"), frozenset({"owner"}))

    entity_id = client.post("/entities", json=VEHICLE_PAYLOAD).json()["id"]

    owner_resp = client.post(f"/entities/{entity_id}/archive")
    assert owner_resp.status_code == 200
    client.post(f"/entities/{entity_id}/restore")

    set_session_role("member")
    member_resp = client.post(f"/entities/{entity_id}/archive")
    assert member_resp.status_code == 403


def test_registry_entry_blocks_disallowed_role_on_create(client, monkeypatch):
    """POST /entities wires its permission check via a route-level
    `dependencies=[Depends(require_entity_create_permission)]` rather than
    a function-parameter Depends() like every other mutating route — prove
    that wiring style enforces a registry entry too, not just the
    path-id-based one covered above.
    """
    monkeypatch.setitem(PERMISSIONS, ("vehicle", "create"), frozenset({"owner"}))

    owner_resp = client.post("/entities", json=VEHICLE_PAYLOAD)
    assert owner_resp.status_code == 201

    set_session_role("member")
    member_resp = client.post("/entities", json=VEHICLE_PAYLOAD)
    assert member_resp.status_code == 403
