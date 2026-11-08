from celery import shared_task

from resources.providers.storage import StorageProviderError, delete_object


@shared_task
def ping():
    """Smoke-test task proving the Celery worker is running. Remove once real tasks exist."""
    return "pong"


@shared_task(autoretry_for=(StorageProviderError,), retry_backoff=True, max_retries=5)
def delete_s3_object(s3_object_key):
    """Delete a file from S3 after its database row is gone. Safe to repeat."""
    delete_object(s3_object_key)