from botocore.client import BaseClient

from app.config import settings
from aria_shared.s3 import build_s3_client

_client: BaseClient | None = None


def get_client() -> BaseClient:
    global _client
    if _client is None:
        _client = build_s3_client(
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            region=settings.s3_region,
        )
    return _client


def download(key: str) -> bytes:
    return get_client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def delete(key: str) -> None:
    get_client().delete_object(Bucket=settings.s3_bucket, Key=key)
