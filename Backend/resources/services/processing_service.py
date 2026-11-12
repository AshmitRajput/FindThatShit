"""Processing request and worker pipeline."""
import hashlib
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from resources.exceptions import (
    AlreadyProcessedError,
    FileNotUploadedError,
    NothingToProcessError,
    StorageUnavailableError,
)
from resources.extractors.documents import ExtractionError, extract_text
from resources.models import (
    Embedding, File, JobStage, JobStatus, ProcessingJob, ProcessingStatus,
    Resource, ResourceType, SourceContent,
)
from resources.providers import ai, storage
from resources.services import analysis_schema, content_service, resource_service
from resources.services.content_service import NormalizedContent

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING)


class ProcessingError(Exception):
    def __init__(self, error_code, message, retryable=False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class RetryableProcessingError(ProcessingError):
    def __init__(self, error_code, message):
        super().__init__(error_code, message, retryable=True)


def check_processable(resource):
    current_file = resource.files.order_by("-created_at").first()
    if current_file:
        try:
            if not storage.object_exists(current_file.s3_object_key):
                raise FileNotUploadedError()
        except storage.StorageProviderError as exc:
            logger.exception("Storage check failed before processing")
            raise StorageUnavailableError() from exc
        return
    source = resource.source_content
    if not (resource.resource_url or source.post_text or source.transcript):
        raise NothingToProcessError()


def request_processing(resource, force=False):
    with transaction.atomic():
        locked = Resource.objects.select_for_update().get(pk=resource.pk)
        job = locked.processing_jobs.filter(status__in=ACTIVE_STATUSES).first()
        if job is None:
            if locked.processing_status == ProcessingStatus.PROCESSED and not force:
                raise AlreadyProcessedError()
            job = ProcessingJob.objects.create(resource=locked, idempotency_key=uuid.uuid4().hex)
            Resource.objects.filter(pk=locked.pk).update(processing_status=ProcessingStatus.PROCESSING)
        enqueue_job(job.pk)
    return job


def enqueue_job(job_id):
    def _enqueue():
        from resources.tasks import process_resource
        try:
            process_resource.delay(str(job_id))
        except Exception:
            logger.exception("Could not enqueue processing job %s", job_id)
    transaction.on_commit(_enqueue)


def get_job_for_user(user, job_id):
    return ProcessingJob.objects.select_related("resource").filter(
        pk=job_id, resource__user=user
    ).first()


def processing_status_for(resource):
    job = resource.processing_jobs.order_by("-created_at").first()
    return {
        "resource_id": str(resource.pk),
        "processing_status": resource.processing_status,
        "job_id": str(job.pk) if job else None,
        "stage": job.stage if job else None,
        "attempt": job.attempt if job else 0,
        "error_code": (job.error_code or None) if job else None,
        "is_retryable": job.is_retryable if job else False,
        "last_processed_at": resource.last_processed_at,
    }


def claim_job(job_id):
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.PROCESSING_STALE_AFTER_SECONDS)
    with transaction.atomic():
        job = (
            ProcessingJob.objects.select_for_update(skip_locked=True)
            .filter(pk=job_id)
            .filter(Q(status=JobStatus.PENDING) | Q(status=JobStatus.RUNNING, started_at__lt=stale_before))
            .first()
        )
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        job.stage = JobStage.EXTRACT_SOURCE
        job.attempt += 1
        job.started_at = now
        job.completed_at = None
        job.error_code, job.error_message, job.is_retryable = "", "", False
        job.save()
        Resource.objects.filter(pk=job.resource_id).update(processing_status=ProcessingStatus.PROCESSING)
    return job


def run_job(job_id, final_attempt):
    job = claim_job(job_id)
    if job is None:
        return
    try:
        resource, content = _load_content(job)
        analysis = _analyze(job, content)
        _save_analysis(job, analysis)
        _embed(job, content)
    except ProcessingError as exc:
        _handle_failure(job, exc, final_attempt)
        if exc.retryable and not final_attempt:
            raise RetryableProcessingError(exc.error_code, str(exc))
    except Exception:
        logger.exception("Unexpected error while processing job %s", job.pk)
        _handle_failure(job, ProcessingError("INTERNAL_ERROR", "Unexpected processing error."), True)
    else:
        _mark_succeeded(job)


def _set_stage(job, stage):
    ProcessingJob.objects.filter(pk=job.pk).update(stage=stage, updated_at=timezone.now())


def _load_resource(job):
    try:
        return Resource.objects.select_related("source_content").prefetch_related("tags", "files").get(
            pk=job.resource_id
        )
    except Resource.DoesNotExist as exc:
        raise ProcessingError("RESOURCE_DELETED", "The resource no longer exists.") from exc


def _load_content(job):
    resource = _load_resource(job)
    source = resource.source_content
    files = sorted(resource.files.all(), key=lambda item: item.created_at, reverse=True)
    document_text = ""
    if files:
        _set_stage(job, JobStage.EXTRACT_DOCUMENT)
        document_text = _read_document(files[0])
    return resource, NormalizedContent(
        source_type=source.source_type, creator=resource.creator, title_hint=resource.title,
        resource_url=resource.resource_url, post_text=source.post_text,
        transcript=source.transcript, document_text=document_text,
    )


def _read_document(file):
    try:
        if not storage.object_exists(file.s3_object_key):
            raise ProcessingError("FILE_NOT_UPLOADED", "The file is not in storage.")
        data = storage.get_object_bytes(file.s3_object_key, settings.MAX_UPLOAD_SIZE_BYTES)
    except storage.StorageObjectTooLargeError as exc:
        raise ProcessingError("FILE_TOO_LARGE", "The stored file is too large.") from exc
    except storage.StorageProviderError as exc:
        raise RetryableProcessingError("STORAGE_ERROR", "File storage is unavailable.") from exc
    if len(data) != file.file_size:
        raise ProcessingError("FILE_SIZE_MISMATCH", "The stored file size does not match the upload.")
    File.objects.filter(pk=file.pk).update(checksum=hashlib.sha256(data).hexdigest())
    try:
        return extract_text(file.mime_type, data)
    except ExtractionError as exc:
        raise ProcessingError(exc.error_code, str(exc)) from exc


def _analyze(job, content):
    _set_stage(job, JobStage.ANALYZE)
    try:
        return analysis_schema.validate_analysis(ai.analyze_resource(content.to_llm_input()))
    except ai.AIProviderError as exc:
        raise ProcessingError(exc.error_code, str(exc), exc.retryable) from exc


def _save_analysis(job, analysis):
    now = timezone.now()
    with transaction.atomic():
        resource = Resource.objects.select_for_update().select_related("user").get(pk=job.resource_id)
        fields = {
            "description": analysis["resource_description"],
            "summary": analysis["summary"],
            "keywords": analysis["keywords"],
            "content_language": analysis["language"],
        }
        if not resource.title and analysis["title"]:
            fields["title"] = analysis["title"]
        if resource.resource_type == ResourceType.OTHER and analysis["resource_type"]:
            fields["resource_type"] = analysis["resource_type"]
        Resource.objects.filter(pk=resource.pk).update(
            **fields, version=F("version") + 1, updated_at=now
        )
        SourceContent.objects.filter(resource_id=resource.pk).update(
            source_description=analysis["source_description"], updated_at=now
        )
        resource_service.add_tags(resource, resource.user, analysis["tags"])


def _embed(job, content):
    _set_stage(job, JobStage.EMBED)
    resource = _load_resource(job)
    text = content_service.build_embedding_text(resource, content)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    model = settings.EMBEDDING_MODEL
    if Embedding.objects.filter(resource_id=resource.pk, model=model, content_hash=content_hash).exists():
        return
    try:
        vector = ai.embed_text(text)
    except ai.AIProviderError as exc:
        code = "EMBEDDING_FAILED" if exc.retryable else exc.error_code
        raise ProcessingError(code, str(exc), exc.retryable) from exc
    Embedding.objects.update_or_create(
        resource_id=resource.pk, model=model,
        defaults={"vector": vector, "content_hash": content_hash},
    )


def _mark_succeeded(job):
    now = timezone.now()
    with transaction.atomic():
        ProcessingJob.objects.filter(pk=job.pk).update(
            status=JobStatus.SUCCEEDED, completed_at=now, updated_at=now
        )
        Resource.objects.filter(pk=job.resource_id).update(
            processing_status=ProcessingStatus.PROCESSED, last_processed_at=now
        )


def _handle_failure(job, exc, final_attempt):
    will_retry = exc.retryable and not final_attempt
    now = timezone.now()
    with transaction.atomic():
        ProcessingJob.objects.filter(pk=job.pk).update(
            status=JobStatus.PENDING if will_retry else JobStatus.FAILED,
            error_code=exc.error_code, error_message=str(exc)[:1000],
            is_retryable=exc.retryable, completed_at=None if will_retry else now, updated_at=now,
        )
        if not will_retry:
            Resource.objects.filter(pk=job.resource_id).update(processing_status=ProcessingStatus.FAILED)
