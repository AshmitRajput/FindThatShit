from django.db import IntegrityError, transaction
from django.test import TestCase

from resources.models import Embedding, IdempotencyKey, ProcessingJob
from resources.tests.helpers import make_user
from resources.tests.phase3_helpers import make_job, make_resource, vector


class Phase3ModelTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.resource = make_resource(self.user)

    def test_defaults(self):
        job = make_job(self.resource)
        self.assertEqual((job.status, job.stage, job.attempt, job.is_retryable), ("PENDING", "EXTRACT_SOURCE", 0, False))
        self.assertEqual((self.resource.summary, self.resource.content_language), ("", ""))

    def test_invalid_job_status_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_job(self.resource, status="BOGUS")

    def test_only_one_active_job_per_resource(self):
        make_job(self.resource)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_job(self.resource)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_job(self.resource, status="RUNNING")

    def test_new_job_allowed_after_finished_jobs(self):
        make_job(self.resource, status="SUCCEEDED")
        make_job(self.resource, status="FAILED")
        make_job(self.resource, status="PENDING")
        self.assertEqual(ProcessingJob.objects.filter(resource=self.resource).count(), 3)

    def test_job_idempotency_key_unique(self):
        other = make_resource(self.user)
        ProcessingJob.objects.create(resource=self.resource, idempotency_key="same")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcessingJob.objects.create(resource=other, idempotency_key="same")

    def test_embedding_unique_per_resource_and_model(self):
        Embedding.objects.create(resource=self.resource, vector=vector(), model="m", content_hash="h")
        Embedding.objects.create(resource=self.resource, vector=vector(), model="m2", content_hash="h")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Embedding.objects.create(resource=self.resource, vector=vector(), model="m", content_hash="h2")

    def test_idempotency_key_unique_per_user_and_endpoint(self):
        kwargs = dict(key="k", endpoint="POST /resources", request_hash="h")
        IdempotencyKey.objects.create(user=self.user, **kwargs)
        IdempotencyKey.objects.create(user=make_user("b@example.com"), **kwargs)
        IdempotencyKey.objects.create(user=self.user, **{**kwargs, "endpoint": "POST /other"})
        with self.assertRaises(IntegrityError), transaction.atomic():
            IdempotencyKey.objects.create(user=self.user, **kwargs)

    def test_deleting_resource_cascades_jobs_and_embeddings(self):
        make_job(self.resource)
        Embedding.objects.create(resource=self.resource, vector=vector(), model="m", content_hash="h")
        self.resource.delete()
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(Embedding.objects.count(), 0)
