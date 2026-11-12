from unittest import mock

from rest_framework.test import APIClient

from resources import tasks
from resources.models import File, ProcessingJob, Resource
from resources.providers.storage import StorageProviderError
from resources.services import processing_service
from resources.tests.helpers import ApiTestCase
from resources.tests.phase3_helpers import make_job


class ProcessApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(tasks.process_resource, "delay")
        self.delay = patcher.start()
        self.addCleanup(patcher.stop)
        self.resource_id = self.create_resource(post_text="Free resume templates")["id"]

    def process(self, resource_id=None, client=None, body=None, **headers):
        return (client or self.client).post(
            self.detail_url(resource_id or self.resource_id, "/process"), body or {}, format="json", **headers
        )

    def test_process_returns_202_creates_job_and_queues_it_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.process()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(set(response.data), {"job_id", "resource_id", "processing_status"})
        self.assertEqual(response.data["processing_status"], "PROCESSING")
        job = ProcessingJob.objects.get(pk=response.data["job_id"])
        self.assertEqual(job.status, "PENDING")
        self.assertEqual(Resource.objects.get(pk=self.resource_id).processing_status, "PROCESSING")
        self.delay.assert_called_once_with(str(job.pk))

    def test_url_only_resource_is_accepted(self):
        resource_id = self.create_resource(resource_url="https://example.com/x")["id"]
        self.assertEqual(self.process(resource_id).status_code, 202)

    def test_nothing_to_process_is_rejected(self):
        resource_id = self.create_resource()["id"]
        response = self.process(resource_id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "NOTHING_TO_PROCESS")

    def add_file(self):
        File.objects.create(
            resource_id=self.resource_id, s3_object_key="findthatshit/k.txt", filename="k.txt",
            mime_type="text/plain", file_size=3,
        )

    def test_file_not_in_storage_is_a_conflict(self):
        self.add_file()
        with mock.patch.object(processing_service.storage, "object_exists", return_value=False):
            response = self.process()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "FILE_NOT_UPLOADED")
        self.assertFalse(ProcessingJob.objects.exists())

    def test_storage_failure_is_a_502(self):
        self.add_file()
        with mock.patch.object(processing_service.storage, "object_exists", side_effect=StorageProviderError("x")):
            response = self.process()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["error"]["code"], "STORAGE_ERROR")

    def test_file_in_storage_is_accepted(self):
        self.add_file()
        with mock.patch.object(processing_service.storage, "object_exists", return_value=True):
            self.assertEqual(self.process().status_code, 202)

    def test_second_request_while_active_returns_the_same_job(self):
        first, second = self.process(), self.process()
        self.assertEqual(first.data["job_id"], second.data["job_id"])
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_processed_resource_needs_force(self):
        Resource.objects.filter(pk=self.resource_id).update(processing_status="PROCESSED")
        response = self.process()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "ALREADY_PROCESSED")
        self.assertEqual(self.process(body={"force": True}).status_code, 202)

    def test_failed_resource_can_be_requeued(self):
        Resource.objects.filter(pk=self.resource_id).update(processing_status="FAILED")
        make_job(Resource.objects.get(pk=self.resource_id), status="FAILED")
        self.assertEqual(self.process().status_code, 202)

    def test_invalid_force_value_is_a_validation_error(self):
        response = self.process(body={"force": "maybe"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")

    def test_other_users_and_anonymous_cannot_use_it(self):
        for response in (
            self.process(client=self.other_client),
            self.other_client.get(self.detail_url(self.resource_id, "/processing")),
        ):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data["error"]["code"], "NOT_FOUND")
        anonymous = APIClient()
        self.assertEqual(anonymous.post(self.detail_url(self.resource_id, "/process"), {}, format="json").status_code, 401)
        self.assertEqual(anonymous.get(self.detail_url(self.resource_id, "/processing")).status_code, 401)
        self.assertFalse(ProcessingJob.objects.exists())

    def test_process_is_rate_limited(self):
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES", {"process": "2/min"}):
            codes = [self.process().status_code for _ in range(3)]
        self.assertEqual(codes, [202, 202, 429])

    def test_enqueue_failure_does_not_fail_the_request(self):
        self.delay.side_effect = RuntimeError("broker down")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.process().status_code, 202)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_processing_status_without_jobs(self):
        data = self.client.get(self.detail_url(self.resource_id, "/processing")).data
        self.assertEqual(data["resource_id"], self.resource_id)
        self.assertEqual(data["processing_status"], "UPLOADED")
        self.assertEqual((data["job_id"], data["stage"], data["attempt"], data["error_code"]), (None, None, 0, None))
        self.assertFalse(data["is_retryable"])
        self.assertIsNone(data["last_processed_at"])

    def test_processing_status_reports_the_latest_job_error(self):
        job = make_job(
            Resource.objects.get(pk=self.resource_id), status="FAILED", stage="ANALYZE",
            attempt=2, error_code="PROVIDER_TIMEOUT", is_retryable=True,
        )
        data = self.client.get(self.detail_url(self.resource_id, "/processing")).data
        self.assertEqual(data["job_id"], str(job.pk))
        self.assertEqual((data["stage"], data["attempt"], data["error_code"], data["is_retryable"]),
                         ("ANALYZE", 2, "PROVIDER_TIMEOUT", True))
