"""Upload/download rules and orchestration around the storage provider."""
import logging
import os
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from resources.exceptions import (
    FileNotUploadedError,
    FileTooLargeError,
    ResourceFileNotFoundError,
    StorageUnavailableError,
    UnsupportedFileTypeError,
)
from resources.models import File, Resource, ResourceType
from resources.providers import storage
from resources.providers.storage import StorageProviderError

logger = logging.getLogger(__name__)

# MIME type -> file extension. Add formats here only.
ALLOWED_UPLOAD_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
}
RESOURCE_TYPE_BY_EXTENSION = {"pdf": ResourceType.PDF, "docx": ResourceType.DOCUMENT, "txt": ResourceType.DOCUMENT}


def validate_upload(mime_type, size):
    if mime_type not in ALLOWED_UPLOAD_TYPES:
        raise UnsupportedFileTypeError()
    if size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise FileTooLargeError()


def sanitize_filename(filename, extension):
    name = os.path.basename(filename.replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if name in ("", ".", ".."):
        name = f"resource.{extension}"
    return name[:255]


def build_object_key(user_id, resource_id, extension):
    return f"{settings.AWS_S3_KEY_PREFIX}/users/{user_id}/resources/{resource_id}/resource.{extension}"


def schedule_object_deletion(s3_object_key):
    """Delete the S3 object in the background, only after the surrounding transaction commits."""

    def enqueue():
        from resources.tasks import delete_s3_object  # local import avoids a circular import

        try:
            delete_s3_object.delay(s3_object_key)
        except Exception:  # the DB change is already committed; never fail the request
            logger.exception("Could not enqueue S3 deletion")

    transaction.on_commit(enqueue)


def create_upload_for_resource(resource, filename, mime_type, size):
    """Validate, presign a PUT URL, and record the (single) file row for the resource."""
    validate_upload(mime_type, size)
    extension = ALLOWED_UPLOAD_TYPES[mime_type]
    safe_name = sanitize_filename(filename, extension)
    key = build_object_key(resource.user_id, resource.id, extension)
    ttl = settings.S3_PRESIGN_UPLOAD_TTL_SECONDS

    try:
        upload_url = storage.create_upload_url(key, mime_type, size, ttl)
    except StorageProviderError:
        logger.exception("Presigning upload URL failed")
        raise StorageUnavailableError()

    with transaction.atomic():
        locked = Resource.objects.select_for_update().get(pk=resource.pk)
        old_keys = [f.s3_object_key for f in locked.files.all() if f.s3_object_key != key]
        locked.files.all().delete()  # MVP rule: one file per resource; a new request replaces the old one
        File.objects.create(resource=locked, s3_object_key=key, filename=safe_name, mime_type=mime_type, file_size=size)
        if locked.resource_type == ResourceType.OTHER:
            Resource.objects.filter(pk=locked.pk).update(resource_type=RESOURCE_TYPE_BY_EXTENSION[extension])
        for old_key in old_keys:
            schedule_object_deletion(old_key)

    return {
        "upload_url": upload_url,
        "method": "PUT",
        "headers": {"Content-Type": mime_type},
        "s3_object_key": key,
        "expires_at": timezone.now() + timedelta(seconds=ttl),
    }


def create_download_for_resource(resource):
    file = resource.files.order_by("-created_at").first()
    if file is None:
        raise ResourceFileNotFoundError()
    try:
        if not storage.object_exists(file.s3_object_key):
            raise FileNotUploadedError()
        url = storage.create_download_url(
            file.s3_object_key, file.filename, settings.S3_PRESIGN_DOWNLOAD_TTL_SECONDS
        )
    except StorageProviderError:
        logger.exception("Creating download URL failed")
        raise StorageUnavailableError()
    return {"url": url, "expires_at": timezone.now() + timedelta(seconds=settings.S3_PRESIGN_DOWNLOAD_TTL_SECONDS)}