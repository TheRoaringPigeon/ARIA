from app.indexes import ensure_indexes


async def test_ensure_indexes_creates_household_id_compound_index(mock_db):
    await ensure_indexes(mock_db)

    info = await mock_db.entities.index_information()
    assert "household_id_1_archived_at_1_domain_1" in info
    assert info["household_id_1_archived_at_1_domain_1"]["key"] == [
        ("household_id", 1),
        ("archived_at", 1),
        ("domain", 1),
    ]


async def test_ensure_indexes_creates_users_indexes(mock_db):
    await ensure_indexes(mock_db)

    info = await mock_db.users.index_information()
    assert info["email_1"]["key"] == [("email", 1)]
    assert info["email_1"]["unique"] is True
    assert info["household_id_1"]["key"] == [("household_id", 1)]
    assert info["notify_overdue_email_1"]["key"] == [("notify_overdue_email", 1)]


async def test_ensure_indexes_creates_logs_indexes(mock_db):
    await ensure_indexes(mock_db)

    info = await mock_db.logs.index_information()
    assert info["household_id_1_entity_id_1_occurred_at_-1"]["key"] == [
        ("household_id", 1),
        ("entity_id", 1),
        ("occurred_at", -1),
    ]
    assert info["household_id_1_schedule_id_1_occurred_at_-1"]["key"] == [
        ("household_id", 1),
        ("schedule_id", 1),
        ("occurred_at", -1),
    ]


async def test_ensure_indexes_creates_schedules_indexes(mock_db):
    await ensure_indexes(mock_db)

    info = await mock_db.schedules.index_information()
    assert info["household_id_1_entity_id_1"]["key"] == [
        ("household_id", 1),
        ("entity_id", 1),
    ]
    due_soon_key = "household_id_1_active_1_pending_delete_at_1_interval_type_1_next_due_at_1"
    assert info[due_soon_key]["key"] == [
        ("household_id", 1),
        ("active", 1),
        ("pending_delete_at", 1),
        ("interval_type", 1),
        ("next_due_at", 1),
    ]


async def test_ensure_indexes_creates_documents_index(mock_db):
    await ensure_indexes(mock_db)

    info = await mock_db.documents.index_information()
    assert info["household_id_1_entity_ids_1_uploaded_at_-1"]["key"] == [
        ("household_id", 1),
        ("entity_ids", 1),
        ("uploaded_at", -1),
    ]


async def test_ensure_indexes_is_idempotent(mock_db):
    await ensure_indexes(mock_db)
    await ensure_indexes(mock_db)

    info = await mock_db.entities.index_information()
    assert "household_id_1_archived_at_1_domain_1" in info

    users_info = await mock_db.users.index_information()
    assert "email_1" in users_info


async def test_users_email_unique_index_rejects_duplicate(mock_db):
    await ensure_indexes(mock_db)

    await mock_db.users.insert_one({"_id": "u1", "email": "dup@example.com"})

    from pymongo.errors import DuplicateKeyError

    try:
        await mock_db.users.insert_one({"_id": "u2", "email": "dup@example.com"})
        assert False, "expected DuplicateKeyError"
    except DuplicateKeyError:
        pass
