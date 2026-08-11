from contextlib import asynccontextmanager
from typing import AsyncIterator

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorClientSession,
    AsyncIOMotorDatabase,
)

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongo_db_name]


@asynccontextmanager
async def start_transaction() -> AsyncIterator[AsyncIOMotorClientSession]:
    """Wraps client.start_session()/session.start_transaction() — callers
    pass the yielded session as `session=` to every write inside the `async
    with` block so they commit or roll back together (a raised exception
    aborts the whole transaction, not just stops partway). Requires Mongo
    running as a replica set (see docker-compose.yml's mongo/mongo-init
    services and mongo_uri's replicaSet=rs0) — a bare standalone mongod
    can't do multi-document transactions at all.

    Routes access this through app/dependencies.py's get_transaction_dep(),
    not this function directly, so tests can override it wholesale — see
    that dependency's docstring for why.
    """
    client = get_client()
    async with await client.start_session() as session:
        async with session.start_transaction():
            yield session
