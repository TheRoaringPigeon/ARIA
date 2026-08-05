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
        #
        # boto3's default retry mode ("legacy") only allows 4 retries with
        # short backoff, which isn't enough to ride out a transient
        # SlowDown/SlowDownWrite response (e.g. MinIO's per-drive
        # health-check throttling under I/O latency spikes) on a multi-MB
        # upload — "standard" mode's longer, jittered exponential backoff
        # gives that kind of blip time to clear.
        config=Config(
            s3={"addressing_style": "path"} if endpoint_url else {},
            retries={"mode": "standard", "max_attempts": 8},
        ),
    )
