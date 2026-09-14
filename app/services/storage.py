import hashlib
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import Settings, get_settings


def content_key(data: bytes) -> str:
    """Return the storage key for these bytes.

    Keys are the SHA-256 of the content, so uploading the same file twice writes the
    same object rather than a duplicate.
    """
    return f"documents/{hashlib.sha256(data).hexdigest()}"


class ObjectStorage:
    """Thin wrapper over the S3 API.

    MinIO implements the same API, so local development and AWS differ only by the
    endpoint and where credentials come from. There is one code path, and it is the one
    that runs in production.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist yet. Safe to run any number of times.

        For local and CI object storage. On AWS the bucket belongs to Terraform, and
        there `BucketAlreadyExists` would mean another account owns the name: an error,
        not something to wave through.
        """
        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            }:
                raise


def build_storage(settings: Settings) -> ObjectStorage:
    # A MinIO endpoint locally; empty on AWS, where boto3 resolves S3 itself.
    endpoint = settings.s3_endpoint or None
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        # Unset on AWS, which leaves boto3 to its default credential chain: in a pod, the IAM
        # role its service account names.
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        region_name=settings.s3_region,
        # MinIO serves buckets as a path rather than a DNS subdomain; S3 keeps its default.
        config=Config(s3={"addressing_style": "path"}) if endpoint else None,
    )
    return ObjectStorage(client, settings.s3_bucket)


if __name__ == "__main__":
    # Prepares local and CI object storage; Compose and CI run `python -m app.services.storage`.
    settings = get_settings()
    build_storage(settings).ensure_bucket()
    print(f"bucket ready: {settings.s3_bucket}")
