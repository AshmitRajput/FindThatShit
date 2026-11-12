from django.contrib import admin

from resources.models import Embedding, File, ProcessingJob, Resource, SourceContent, Tag


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "platform", "resource_type", "processing_status", "created_at")
    list_filter = ("platform", "resource_type", "processing_status")
    search_fields = ("title", "creator", "source_url", "user__email")
    raw_id_fields = ("user",)


admin.site.register(SourceContent)
admin.site.register(File)
admin.site.register(Tag)


@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("id", "resource", "status", "stage", "attempt", "error_code", "created_at")
    list_filter = ("status", "stage")


admin.site.register(Embedding)
