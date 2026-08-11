import pytest

import app.db as db_module
from app.db import start_transaction


class _FakeTransactionCM:
    """Stands in for the object session.start_transaction() returns —
    entering/exiting it is what would tell a real Mongo session to commit
    (clean exit) or abort (exception), so what matters here is *that* both
    happen and in what order, not their real transactional effect.
    """

    def __init__(self, calls: list[str]):
        self._calls = calls

    def __enter__(self):
        self._calls.append("transaction_entered")
        return None

    def __exit__(self, exc_type, exc, tb):
        self._calls.append("transaction_exited")
        return False  # never swallow — a real Mongo transaction needs the
        # exception to propagate out in order to abort instead of commit.


class _FakeSession:
    def __init__(self, calls: list[str]):
        self._calls = calls

    def __enter__(self):
        self._calls.append("session_entered")
        return self

    def __exit__(self, exc_type, exc, tb):
        self._calls.append("session_exited")
        return False

    def start_transaction(self):
        return _FakeTransactionCM(self._calls)


class _FakeClient:
    def __init__(self):
        self.calls: list[str] = []

    def start_session(self):
        self.calls.append("start_session_called")
        return _FakeSession(self.calls)


def test_start_transaction_control_flow(monkeypatch):
    """Proves start_transaction() calls client.start_session() then
    session.start_transaction(), in that order, and yields the session.
    """
    client = _FakeClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)

    with start_transaction() as session:
        assert client.calls == ["start_session_called", "session_entered", "transaction_entered"]
        assert isinstance(session, _FakeSession)

    assert client.calls == [
        "start_session_called",
        "session_entered",
        "transaction_entered",
        "transaction_exited",
        "session_exited",
    ]


def test_start_transaction_propagates_exceptions(monkeypatch):
    """An exception raised inside the `with` block must propagate out rather
    than being swallowed — that's the signal a real Mongo session needs to
    abort the transaction instead of committing it. The worker's hand-rolled
    FakeCollection (tests/fakes.py) can't prove real rollback any more than
    mongomock can for core-api, so this control-flow guarantee is the
    closest a unit test gets; actual rollback is verified live — see
    docs/scaling-debt-plans/sd6a-entity-trash-cascade-transactions.md.
    """
    client = _FakeClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)

    with pytest.raises(RuntimeError, match="boom"):
        with start_transaction():
            raise RuntimeError("boom")

    assert client.calls == [
        "start_session_called",
        "session_entered",
        "transaction_entered",
        "transaction_exited",
        "session_exited",
    ]
