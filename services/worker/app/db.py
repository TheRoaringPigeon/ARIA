from contextlib import contextmanager
from typing import Iterator

from pymongo import MongoClient
from pymongo.client_session import ClientSession
from pymongo.database import Database

from app.config import settings

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongo_uri)
    return _client


def get_db() -> Database:
    return get_client()[settings.mongo_db_name]


@contextmanager
def start_transaction() -> Iterator[ClientSession]:
    """Sync counterpart of core-api's app/db.py's start_transaction() — same
    client.start_session()/session.start_transaction() shape, just without
    async/await since worker uses plain pymongo, not motor. Callers pass the
    yielded session as `session=` to every write inside the `with` block so
    they commit or roll back together; a raised exception aborts the whole
    transaction rather than leaving it partially applied.

    Requires Mongo running as a replica set (see docker-compose.yml's
    mongo/mongo-init services and mongo_uri's replicaSet=rs0) — same
    requirement as core-api's, now available to worker too since that infra
    already exists. See
    docs/scaling-debt-plans/sd6a-entity-trash-cascade-transactions.md for why
    this is a hard server-side requirement, not a driver setting.
    """
    client = get_client()
    with client.start_session() as session:
        with session.start_transaction():
            yield session
