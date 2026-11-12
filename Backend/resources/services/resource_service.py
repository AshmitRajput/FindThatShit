"""Resource use cases. Views call these; they own transactions and ownership scoping."""
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from resources.exceptions import VersionConflictError
from resources.models import (
    PLATFORM_BY_SOURCE_TYPE,
    Resource,
    ResourceType,
    SourceContent,
    Tag,
)
from resources.services import storage_service

SORT_FIELDS = {"date_added": "created_at", "-date_added": "-created_at", "title": "title", "-title": "-title"}
RESOURCE_FIELDS = ("title", "creator", "source_url", "resource_url", "resource_type", "description")
SOURCE_CONTENT_FIELDS = ("post_text", "transcript")


def base_queryset(user):
    """The ONLY entry point for reading resources. Always scoped to the user. Reuse it (e.g. for search)."""
    return (
        Resource.objects.filter(user=user)
        .select_related("source_content")
        .prefetch_related("tags", "files")
    )


def get_resource_for_user(user, resource_id):
    return base_queryset(user).get(pk=resource_id)


def infer_resource_type(resource_url):
    host = (urlparse(resource_url).hostname or "").lower() if resource_url else ""
    if not host:
        return ResourceType.OTHER
    if host == "notion.so" or host.endswith(".notion.so") or host.endswith(".notion.site"):
        return ResourceType.NOTION
    if host in ("drive.google.com", "docs.google.com"):
        return ResourceType.GOOGLE_DRIVE
    if host == "github.com" or host.endswith(".github.com"):
        return ResourceType.GITHUB
    if host == "dropbox.com" or host.endswith(".dropbox.com"):
        return ResourceType.DROPBOX
    return ResourceType.WEBSITE


def set_tags(resource, user, names):
    """Replace the resource's tags with `names` (case-insensitive, de-duplicated)."""
    unique = {}
    for name in names:
        clean = " ".join(name.split())
        unique.setdefault(clean.lower(), clean)
    tags = [
        Tag.objects.get_or_create(user=user, normalized_name=normalized, defaults={"name": display})[0]
        for normalized, display in unique.items()
    ]
    resource.tags.set(tags)


def add_tags(resource, user, names):
    existing = [tag.name for tag in resource.tags.all()]
    set_tags(resource, user, existing + list(names))


def create_resource(user, data):
    resource_url = data.get("resource_url", "")
    with transaction.atomic():
        resource = Resource.objects.create(
            user=user,
            title=data.get("title", ""),
            creator=data.get("creator", ""),
            platform=PLATFORM_BY_SOURCE_TYPE[data["source_type"]],
            source_url=data["source_url"],
            resource_url=resource_url,
            resource_type=data.get("resource_type") or infer_resource_type(resource_url),
        )
        SourceContent.objects.create(
            resource=resource,
            source_type=data["source_type"],
            post_text=data.get("post_text", ""),
            transcript=data.get("transcript", ""),
        )
        set_tags(resource, user, data.get("tags", []))
    return get_resource_for_user(user, resource.pk)


def update_resource(user, resource, data):
    """Optimistic-locking update: `data["version"]` must equal the stored version, else 409."""
    expected_version = data["version"]
    fields = {name: data[name] for name in RESOURCE_FIELDS if name in data}
    source_fields = {name: data[name] for name in SOURCE_CONTENT_FIELDS if name in data}
    now = timezone.now()
    with transaction.atomic():
        updated = Resource.objects.filter(pk=resource.pk, user=user, version=expected_version).update(
            **fields, version=F("version") + 1, updated_at=now
        )
        if not updated:
            raise VersionConflictError()
        if source_fields:
            SourceContent.objects.filter(resource_id=resource.pk).update(updated_at=now, **source_fields)
        if "tags" in data:
            set_tags(resource, user, data["tags"])
    return get_resource_for_user(user, resource.pk)


def delete_resource(resource):
    keys = [file.s3_object_key for file in resource.files.all()]
    with transaction.atomic():
        resource.delete()  # cascades to source content, files, tag links
        for key in keys:
            storage_service.schedule_object_deletion(key)


def list_resources_for_user(user, filters):
    queryset = base_queryset(user)
    if filters.get("platform"):
        queryset = queryset.filter(platform=filters["platform"])
    if filters.get("source_type"):
        queryset = queryset.filter(source_content__source_type=filters["source_type"])
    if filters.get("resource_type"):
        queryset = queryset.filter(resource_type=filters["resource_type"])
    if filters.get("processing_status"):
        queryset = queryset.filter(processing_status=filters["processing_status"])
    if filters.get("tag"):
        queryset = queryset.filter(tags__normalized_name=" ".join(filters["tag"].split()).lower())
    if filters.get("creator"):
        queryset = queryset.filter(creator__icontains=filters["creator"])
    return queryset.order_by(SORT_FIELDS[filters.get("sort", "-date_added")], "id")