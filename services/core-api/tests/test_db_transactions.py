import pytest

import app.db as db_module
from app.db import start_transaction
from app.dependencies import get_transaction_dep


class _FakeTransactionCM:
    """Stands in for the object session.start_transaction() returns —
    entering/exiting it is what would tell a real Mongo session to commit
    (clean exit) or abort (exception), so what matters here is *that* both
    happen and in what order, not their real transactional effect.
    """

    def __init__(self, calls: list[str]):
        self._calls = calls

    async def __aenter__(self):
        self._calls.append("transaction_entered")
        return None

    async def __aexit__(self, exc_type, exc, tb):
        self._calls.append("transaction_exited")
        return False  # never swallow — a real Mongo transaction needs the
        # exception to propagate out in order to abort instead of commit.


class _FakeSession:
    def __init__(self, calls: list[str]):
        self._calls = calls

    async def __aenter__(self):
        self._calls.append("session_entered")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._calls.append("session_exited")
        return False

    def start_transaction(self):
        return _FakeTransactionCM(self._calls)


class _FakeClient:
    def __init__(self):
        self.calls: list[str] = []

    async def start_session(self):
        self.calls.append("start_session_called")
        return _FakeSession(self.calls)


async def test_start_transaction_control_flow(monkeypatch):
    """Proves start_transaction() calls client.start_session() then
    session.start_transaction(), in that order, and yields the session.
    """
    client = _FakeClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)

    async with start_transaction() as session:
        assert client.calls == ["start_session_called", "session_entered", "transaction_entered"]
        assert isinstance(session, _FakeSession)

    assert client.calls == [
        "start_session_called",
        "session_entered",
        "transaction_entered",
        "transaction_exited",
        "session_exited",
    ]


async def test_start_transaction_propagates_exceptions(monkeypatch):
    """An exception raised inside the `async with` block must propagate out
    rather than being swallowed — that's the signal a real Mongo session
    needs to abort the transaction instead of committing it. mongomock can't
    prove real rollback (see tests/conftest.py's mock_transaction fixture),
    so this control-flow guarantee is the closest a unit test gets; actual
    rollback is verified live — see
    docs/scaling-debt-plans/sd6a-entity-trash-cascade-transactions.md.
    """
    client = _FakeClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)

    with pytest.raises(RuntimeError, match="boom"):
        async with start_transaction():
            raise RuntimeError("boom")

    assert client.calls == [
        "start_session_called",
        "session_entered",
        "transaction_entered",
        "transaction_exited",
        "session_exited",
    ]


def test_get_transaction_dep_returns_start_transaction():
    """Proves the FastAPI dependency isn't accidentally wired to a
    permanent no-op outside of tests — tests/conftest.py's mock_transaction
    fixture overrides this dependency, but the un-overridden wiring (what
    actually runs against a real Mongo) must resolve to the real
    start_transaction, not a stub.
    """
    assert get_transaction_dep() is start_transaction
