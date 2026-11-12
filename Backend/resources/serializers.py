from rest_framework import serializers

from resources.models import (
    Platform,
    ProcessingStatus,
    Resource,
    ResourceType,
    SourceType,
)
from resources.validators import validate_http_url

MAX_SOURCE_TEXT_LENGTH = 50_000
MAX_DESCRIPTION_LENGTH = 10_000
MAX_TAGS_PER_RESOURCE = 20
SORT_CHOICES = ["date_added", "-date_added", "title", "-title"]


class ResourceFieldsMixin(serializers.Serializer):
    creator = serializers.CharField(max_length=255, required=False, allow_blank=True)
    resource_url = serializers.URLField(
        max_length=2048, required=False, allow_blank=True, validators=[validate_http_url]
    )
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    resource_type = serializers.ChoiceField(choices=ResourceType.choices, required=False)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=50, allow_blank=False),
        required=False,
        max_length=MAX_TAGS_PER_RESOURCE,
    )
    post_text = serializers.CharField(
        max_length=MAX_SOURCE_TEXT_LENGTH, required=False, allow_blank=True
    )
    transcript = serializers.CharField(
        max_length=MAX_SOURCE_TEXT_LENGTH, required=False, allow_blank=True
    )

    def validate_tags(self, value):
        if any(not " ".join(tag.split()) for tag in value):
            raise serializers.ValidationError("Tags cannot be blank.")
        return value


class ResourceCreateSerializer(ResourceFieldsMixin):
    source_type = serializers.ChoiceField(choices=SourceType.choices)
    source_url = serializers.URLField(
        max_length=2048, validators=[validate_http_url]
    )


class ResourceUpdateSerializer(ResourceFieldsMixin):
    version = serializers.IntegerField(min_value=1)
    source_url = serializers.URLField(
        max_length=2048, required=False, validators=[validate_http_url]
    )
    description = serializers.CharField(
        max_length=MAX_DESCRIPTION_LENGTH, required=False, allow_blank=True
    )

    def validate(self, attrs):
        if set(attrs) == {"version"} or not attrs or (
            set(attrs) - {"version"} == set()
        ):
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class UploadUrlRequestSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)
    mime_type = serializers.CharField(max_length=127)
    size = serializers.IntegerField(min_value=1)


class ProcessRequestSerializer(serializers.Serializer):
    force = serializers.BooleanField(required=False, default=False)


class ResourceListQuerySerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=Platform.choices, required=False)
    source_type = serializers.ChoiceField(choices=SourceType.choices, required=False)
    resource_type = serializers.ChoiceField(choices=ResourceType.choices, required=False)
    processing_status = serializers.ChoiceField(choices=ProcessingStatus.choices, required=False)
    tag = serializers.CharField(max_length=50, required=False)
    creator = serializers.CharField(max_length=255, required=False)
    sort = serializers.ChoiceField(choices=SORT_CHOICES, required=False, default="-date_added")


class ResourceSummarySerializer(serializers.ModelSerializer):
    source_type = serializers.CharField(source="source_content.source_type", read_only=True)
    date_added = serializers.DateTimeField(source="created_at", read_only=True)
    tags = serializers.SerializerMethodField()
    has_file = serializers.SerializerMethodField()

    class Meta:
        model = Resource
        fields = (
            "id",
            "title",
            "description",
            "creator",
            "platform",
            "source_type",
            "source_url",
            "resource_url",
            "resource_type",
            "tags",
            "has_file",
            "processing_status",
            "version",
            "date_added",
            "updated_at",
        )
        read_only_fields = fields

    def get_tags(self, obj):
        return [
            tag.name
            for tag in sorted(obj.tags.all(), key=lambda tag: tag.normalized_name)
        ]

    def get_has_file(self, obj):
        return bool(obj.files.all())


class ResourceDetailSerializer(serializers.Serializer):
    def to_representation(self, resource):
        source_content = resource.source_content
        files = sorted(
            list(resource.files.all()),
            key=lambda item: item.created_at,
            reverse=True,
        )
        current_file = files[0] if files else None
        date_field = serializers.DateTimeField()
        return {
            "id": str(resource.id),
            "source": {
                "platform": resource.platform,
                "source_type": source_content.source_type,
                "creator": resource.creator,
                "source_url": resource.source_url,
                "transcript": source_content.transcript,
                "post_text": source_content.post_text,
                "source_description": source_content.source_description,
            },
            "resource": {
                "title": resource.title,
                "description": resource.description,
                "resource_type": resource.resource_type,
                "resource_url": resource.resource_url,
                "file": (
                    {
                        "filename": current_file.filename,
                        "mime_type": current_file.mime_type,
                        "file_size": current_file.file_size,
                        "created_at": date_field.to_representation(current_file.created_at),
                    }
                    if current_file
                    else None
                ),
            },
            "ai_metadata": {
                "tags": [
                    tag.name
                    for tag in sorted(resource.tags.all(), key=lambda tag: tag.normalized_name)
                ],
                "keywords": resource.keywords,
                "summary": resource.summary,
                "language": resource.content_language,
            },
            "system": {
                "date_added": date_field.to_representation(resource.created_at),
                "updated_at": date_field.to_representation(resource.updated_at),
                "processing_status": resource.processing_status,
                "last_processed_at": (
                    date_field.to_representation(resource.last_processed_at)
                    if resource.last_processed_at
                    else None
                ),
                "version": resource.version,
            },
        }
