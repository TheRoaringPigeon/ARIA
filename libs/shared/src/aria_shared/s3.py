import boto3
from botocore.client import BaseClient, Config


def build_s3_client(
    *,
    endpoint_url: str | None,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=region,
        # MinIO needs path-style addressing to resolve without wildcard
        # DNS; real S3 works fine with boto3's virtual-hosted-style
        # default, so this is only forced for a custom endpoint.
        config=Config(s3={"addressing_style": "path"}) if endpoint_url else None,
    )
