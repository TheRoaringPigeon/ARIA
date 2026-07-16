from typing import BinaryIO

from botocore.client import BaseClient
from botocore.exceptions import ClientError

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


def ensure_bucket() -> None:
    client = get_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
            raise
        client.create_bucket(Bucket=settings.s3_bucket)


def upload(key: str, fileobj: BinaryIO, content_type: str) -> None:
    get_client().upload_fileobj(
        fileobj, settings.s3_bucket, key, ExtraArgs={"ContentType": content_type}
    )


def stream(key: str):
    return get_client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"]


def delete(key: str) -> None:
    get_client().delete_object(Bucket=settings.s3_bucket, Key=key)
