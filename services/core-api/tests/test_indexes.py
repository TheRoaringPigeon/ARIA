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


async def test_ensure_indexes_is_idempotent(mock_db):
    await ensure_indexes(mock_db)
    await ensure_indexes(mock_db)

    info = await mock_db.entities.index_information()
    assert "household_id_1_archived_at_1_domain_1" in info
