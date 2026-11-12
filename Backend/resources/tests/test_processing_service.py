import hashlib
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from resources.models import Embedding, File, ProcessingJob, Resource
from resources.providers import ai
from resources.providers.storage import StorageObjectTooLargeError, StorageProviderError
from resources.services import processing_service, resource_service
from resources.services.processing_service import RetryableProcessingError
from resources.tests.helpers import make_user
from resources.tests.phase3_helpers import GOOD_ANALYSIS, make_file, make_job, make_resource, vector


@override_settings(EMBEDDING_MODEL="emb-test")
class RunJobTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.resource = make_resource(self.user)
        self.job = make_job(self.resource)
        patchers = {
            "analyze": mock.patch.object(processing_service.ai, "analyze_resource", return_value=dict(GOOD_ANALYSIS)),
            "embed": mock.patch.object(processing_service.ai, "embed_text", return_value=vector()),
            "exists": mock.patch.object(processing_service.storage, "object_exists", return_value=True),
            "read": mock.patch.object(processing_service.storage, "get_object_bytes", return_value=b""),
        }
        for name, patcher in patchers.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def run_job(self, final=False):
        processing_service.run_job(self.job.pk, final_attempt=final)
        self.job.refresh_from_db()
        self.resource.refresh_from_db()

    def test_url_only_resource_is_processed_without_touching_storage(self):
        self.run_job()
        self.assertEqual(self.job.status, "SUCCEEDED")
        self.assertEqual(self.job.attempt, 1)
        self.assertEqual(self.resource.processing_status, "PROCESSED")
        self.assertEqual(self.resource.description, GOOD_ANALYSIS["resource_description"])
        self.assertEqual(self.resource.summary, GOOD_ANALYSIS["summary"])
        self.assertEqual(self.resource.keywords, GOOD_ANALYSIS["keywords"])
        self.assertEqual(self.resource.content_language, "en")
        self.assertEqual(self.resource.title, GOOD_ANALYSIS["title"])
        self.assertEqual(self.resource.resource_type, "PDF")
        self.assertEqual(self.resource.version, 2)
        self.assertIsNotNone(self.resource.last_processed_at)
        self.assertEqual(self.resource.source_content.source_description, GOOD_ANALYSIS["source_description"])
        self.assertEqual(sorted(t.name for t in self.resource.tags.all()), ["Career", "Resume"])
        self.assertEqual(Embedding.objects.filter(resource=self.resource, model="emb-test").count(), 1)
        self.exists.assert_not_called()
        self.read.assert_not_called()

    def test_file_is_downloaded_checksummed_and_its_text_analysed(self):
        data = b"Software engineer resume"
        file = make_file(self.resource, size=len(data))
        self.read.return_value = data
        self.run_job()
        self.assertEqual(self.resource.processing_status, "PROCESSED")
        self.assertEqual(File.objects.get(pk=file.pk).checksum, hashlib.sha256(data).hexdigest())
        self.assertIn("Software engineer resume", self.analyze.call_args[0][0]["document_text"])

    def test_user_title_is_kept_and_blank_title_is_filled(self):
        Resource.objects.filter(pk=self.resource.pk).update(title="Mine")
        self.run_job()
        self.assertEqual(self.resource.title, "Mine")

    def test_resource_type_changes_only_from_other(self):
        Resource.objects.filter(pk=self.resource.pk).update(resource_type="GITHUB")
        self.run_job()
        self.assertEqual(self.resource.resource_type, "GITHUB")

    def test_user_tags_are_kept_and_ai_tags_added(self):
        resource_service.set_tags(self.resource, self.user, ["mine"])
        self.run_job()
        self.assertEqual(sorted(t.name for t in self.resource.tags.all()), ["Career", "Resume", "mine"])

    def test_claim_is_exclusive(self):
        self.assertIsNotNone(processing_service.claim_job(self.job.pk))
        self.assertIsNone(processing_service.claim_job(self.job.pk))

    def test_stale_running_job_can_be_claimed_again_but_fresh_one_cannot(self):
        ProcessingJob.objects.filter(pk=self.job.pk).update(status="RUNNING", started_at=timezone.now())
        self.assertIsNone(processing_service.claim_job(self.job.pk))
        ProcessingJob.objects.filter(pk=self.job.pk).update(started_at=timezone.now() - timedelta(hours=1))
        claimed = processing_service.claim_job(self.job.pk)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.attempt, 1)

    def test_retryable_error_before_last_attempt_requeues_the_job(self):
        self.analyze.side_effect = ai.AIProviderError("PROVIDER_TIMEOUT", "slow", True)
        with self.assertRaises(RetryableProcessingError):
            processing_service.run_job(self.job.pk, final_attempt=False)
        self.job.refresh_from_db()
        self.resource.refresh_from_db()
        self.assertEqual((self.job.status, self.job.error_code, self.job.is_retryable), ("PENDING", "PROVIDER_TIMEOUT", True))
        self.assertEqual(self.resource.processing_status, "PROCESSING")

    def test_retryable_error_on_last_attempt_fails_without_raising(self):
        self.analyze.side_effect = ai.AIProviderError("PROVIDER_TIMEOUT", "slow", True)
        self.run_job(final=True)
        self.assertEqual(self.job.status, "FAILED")
        self.assertEqual(self.resource.processing_status, "FAILED")
        self.assertIsNotNone(self.job.completed_at)

    def test_non_retryable_error_fails_immediately(self):
        self.analyze.side_effect = ai.AIProviderError("PROVIDER_AUTH_ERROR", "bad key", False)
        self.run_job(final=False)
        self.assertEqual((self.job.status, self.job.error_code), ("FAILED", "PROVIDER_AUTH_ERROR"))
        self.assertEqual(self.resource.processing_status, "FAILED")

    def test_invalid_llm_output_is_retryable(self):
        self.analyze.return_value = {"resource_description": ""}
        with self.assertRaises(RetryableProcessingError):
            processing_service.run_job(self.job.pk, final_attempt=False)
        self.job.refresh_from_db()
        self.assertEqual(self.job.error_code, "LLM_INVALID_OUTPUT")

    def test_embedding_failure_keeps_ai_metadata(self):
        self.embed.side_effect = ai.AIProviderError("PROVIDER_TIMEOUT", "slow", True)
        self.run_job(final=True)
        self.assertEqual((self.job.status, self.job.error_code), ("FAILED", "EMBEDDING_FAILED"))
        self.assertEqual(self.resource.processing_status, "FAILED")
        self.assertEqual(self.resource.description, GOOD_ANALYSIS["resource_description"])
        self.assertEqual(Embedding.objects.count(), 0)

    def test_non_retryable_embedding_error_keeps_its_own_code(self):
        self.embed.side_effect = ai.AIProviderError("EMBEDDING_DIMENSION_MISMATCH", "size", False)
        self.run_job()
        self.assertEqual(self.job.error_code, "EMBEDDING_DIMENSION_MISMATCH")

    def test_missing_file_object_fails_without_retry(self):
        make_file(self.resource)
        self.exists.return_value = False
        self.run_job()
        self.assertEqual((self.job.status, self.job.error_code), ("FAILED", "FILE_NOT_UPLOADED"))

    def test_size_mismatch_fails(self):
        make_file(self.resource, size=99)
        self.read.return_value = b"abc"
        self.run_job()
        self.assertEqual(self.job.error_code, "FILE_SIZE_MISMATCH")

    def test_storage_error_is_retryable(self):
        make_file(self.resource)
        self.exists.side_effect = StorageProviderError("down")
        with self.assertRaises(RetryableProcessingError):
            processing_service.run_job(self.job.pk, final_attempt=False)
        self.job.refresh_from_db()
        self.assertEqual(self.job.error_code, "STORAGE_ERROR")

    def test_oversized_stored_file_fails(self):
        make_file(self.resource)
        self.read.side_effect = StorageObjectTooLargeError("big")
        self.run_job()
        self.assertEqual(self.job.error_code, "FILE_TOO_LARGE")

    def test_unreadable_document_fails_with_extraction_code(self):
        make_file(self.resource, size=4, mime_type="application/pdf")
        self.read.return_value = b"nope"
        self.run_job()
        self.assertEqual((self.job.status, self.job.error_code), ("FAILED", "EXTRACTION_FAILED"))

    def test_unexpected_exception_is_hidden_and_marks_failed(self):
        self.analyze.side_effect = RuntimeError("boom secret")
        self.run_job()
        self.assertEqual((self.job.status, self.job.error_code), ("FAILED", "INTERNAL_ERROR"))
        self.assertNotIn("boom", self.job.error_message)

    def test_resource_deleted_before_or_during_run_does_not_crash(self):
        def delete_then_answer(_content):
            Resource.objects.filter(pk=self.resource.pk).delete()
            return dict(GOOD_ANALYSIS)

        self.analyze.side_effect = delete_then_answer
        processing_service.run_job(self.job.pk, final_attempt=False)
        self.assertFalse(Resource.objects.filter(pk=self.resource.pk).exists())
        processing_service.run_job(self.job.pk, final_attempt=False)  # job is gone: quiet no-op

    def test_embedding_is_skipped_when_content_is_unchanged(self):
        self.run_job()
        make_job(self.resource)
        job_two = ProcessingJob.objects.filter(resource=self.resource, status="PENDING").get()
        processing_service.run_job(job_two.pk, final_attempt=False)
        self.assertEqual(self.embed.call_count, 1)
        self.assertEqual(Embedding.objects.count(), 1)
