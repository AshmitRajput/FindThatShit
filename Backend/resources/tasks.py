from celery import shared_task

from resources.providers.storage import StorageProviderError, delete_object
from resources.services import processing_service
from resources.services.processing_service import RetryableProcessingError


@shared_task
def ping():
    """Smoke-test task proving the Celery worker is running. Remove once real tasks exist."""
    return "pong"


@shared_task(autoretry_for=(StorageProviderError,), retry_backoff=True, max_retries=5)
def delete_s3_object(s3_object_key):
    """Delete a file from S3 after its database row is gone. Safe to repeat."""
    delete_object(s3_object_key)


@shared_task(bind=True, max_retries=3)
def process_resource(self, job_id):
    final_attempt = self.request.retries >= self.max_retries
    try:
        processing_service.run_job(job_id, final_attempt=final_attempt)
    except RetryableProcessingError as exc:
        raise self.retry(exc=exc, countdown=min(30 * 2 ** self.request.retries, 600))