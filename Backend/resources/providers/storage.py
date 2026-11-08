"""Thin boto3 wrapper. The only module that talks to S3. Raises StorageProviderError on any failure."""
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class StorageProviderError(Exception):
    pass


@lru_cache(maxsize=1)
def _client():
    endpoint_url = settings.AWS_S3_ENDPOINT_URL or (
        f"https://s3.{settings.AWS_REGION}.amazonaws.com"
        if settings.AWS_REGION
        else None
    )

    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION or None,
        endpoint_url=endpoint_url,
        config=Config(signature_version="s3v4"),
    )


def _bucket():
    if not settings.AWS_S3_BUCKET_NAME:
        raise StorageProviderError("AWS_S3_BUCKET_NAME is not configured.")
    return settings.AWS_S3_BUCKET_NAME


def create_upload_url(key, content_type, content_length, expires_in):
    """Presigned PUT. Content-Type and Content-Length are signed, so S3 rejects any other type or size."""
    try:
        return _client().generate_presigned_url(
            "put_object",
            Params={"Bucket": _bucket(), "Key": key, "ContentType": content_type, "ContentLength": content_length},
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
    except (BotoCoreError, ClientError) as exc:
        raise StorageProviderError("Could not create upload URL.") from exc


def create_download_url(key, filename, expires_in):
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": key, "ResponseContentDisposition": disposition},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as exc:
        raise StorageProviderError("Could not create download URL.") from exc


def object_exists(key):
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise StorageProviderError("Could not check object.") from exc
    except BotoCoreError as exc:
        raise StorageProviderError("Could not check object.") from exc


def delete_object(key):
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)  # S3 delete is idempotent
    except (BotoCoreError, ClientError) as exc:
        raise StorageProviderError("Could not delete object.") from exc