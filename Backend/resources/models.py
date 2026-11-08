import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Platform(models.TextChoices):
    INSTAGRAM = "INSTAGRAM"
    X = "X"
    OTHER = "OTHER"


class SourceType(models.TextChoices):
    INSTAGRAM_REEL = "INSTAGRAM_REEL"
    INSTAGRAM_POST = "INSTAGRAM_POST"
    X_POST = "X_POST"
    OTHER = "OTHER"


class ResourceType(models.TextChoices):
    PDF = "PDF"
    DOCUMENT = "DOCUMENT"
    SPREADSHEET = "SPREADSHEET"
    NOTION = "NOTION"
    GOOGLE_DRIVE = "GOOGLE_DRIVE"
    GITHUB = "GITHUB"
    DROPBOX = "DROPBOX"
    WEBSITE = "WEBSITE"
    OTHER = "OTHER"


class ProcessingStatus(models.TextChoices):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


PLATFORM_BY_SOURCE_TYPE = {
    SourceType.INSTAGRAM_REEL: Platform.INSTAGRAM,
    SourceType.INSTAGRAM_POST: Platform.INSTAGRAM,
    SourceType.X_POST: Platform.X,
    SourceType.OTHER: Platform.OTHER,
}


class Resource(models.Model):
    """A saved resource (URL or uploaded file) plus its source context. Always owned by one user.

    Optional text fields use "" (not NULL) when not provided.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="resources")
    title = models.CharField(max_length=255, blank=True, default="")
    creator = models.CharField(max_length=255, blank=True, default="")
    platform = models.CharField(max_length=20, choices=Platform.choices)
    source_url = models.URLField(max_length=2048)
    resource_url = models.URLField(max_length=2048, blank=True, default="")
    resource_type = models.CharField(max_length=30, choices=ResourceType.choices, default=ResourceType.OTHER)
    description = models.TextField(blank=True, default="")
    keywords = models.JSONField(default=list, blank=True)  # list[str]; filled by AI processing (Phase 3)
    processing_status = models.CharField(
        max_length=20, choices=ProcessingStatus.choices, default=ProcessingStatus.UPLOADED
    )
    version = models.PositiveIntegerField(default=1)  # optimistic locking
    last_processed_at = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField("Tag", through="ResourceTag", related_name="resources")
    created_at = models.DateTimeField(auto_now_add=True)  # exposed by the API as `date_added`
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "resources"
        indexes = [models.Index(fields=["user", "created_at"], name="resources_user_created_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(platform__in=Platform.values), name="resources_platform_ck"),
            models.CheckConstraint(
                condition=Q(resource_type__in=ResourceType.values), name="resources_resource_type_ck"
            ),
            models.CheckConstraint(
                condition=Q(processing_status__in=ProcessingStatus.values), name="resources_processing_status_ck"
            ),
        ]

    def __str__(self):
        return self.title or str(self.id)


class SourceContent(models.Model):
    """Text taken from the social post: transcript, post text, and the AI source description."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.OneToOneField(Resource, on_delete=models.CASCADE, related_name="source_content")
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    transcript = models.TextField(blank=True, default="")
    post_text = models.TextField(blank=True, default="")
    source_description = models.TextField(blank=True, default="")
    extracted_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "source_contents"
        constraints = [
            models.CheckConstraint(condition=Q(source_type__in=SourceType.values), name="source_contents_source_type_ck"),
        ]


class File(models.Model):
    """Metadata of an uploaded file. Bytes live in S3; only the object key is stored here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="files")
    s3_object_key = models.CharField(max_length=1024, unique=True)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=127)
    file_size = models.PositiveBigIntegerField()
    checksum = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "files"


class Tag(models.Model):
    """A user's tag. `normalized_name` (lowercase, single-spaced) is the uniqueness key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=50)
    normalized_name = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tags"
        constraints = [
            models.UniqueConstraint(fields=["user", "normalized_name"], name="tags_user_id_normalized_name_uq"),
        ]

    def __str__(self):
        return self.name


class ResourceTag(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "resource_tags"
        constraints = [
            models.UniqueConstraint(fields=["resource", "tag"], name="resource_tags_resource_id_tag_id_uq"),
        ]