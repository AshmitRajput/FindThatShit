from unittest import mock

from django.test import override_settings

from resources import tasks
from resources.models import File, Resource
from resources.providers.storage import StorageProviderError
from resources.services import storage_service
from resources.tests.helpers import ApiTestCase

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload_body(filename="Modern_Resume_Final.pdf", mime_type=PDF, size=1234):
    return {"filename": filename, "mime_type": mime_type, "size": size}


@override_settings(AWS_S3_KEY_PREFIX="findthatshit", MAX_UPLOAD_SIZE_BYTES=5000)
class UploadUrlTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.resource_id = self.create_resource()["id"]
        self.url = self.detail_url(self.resource_id, "/upload-url")
        patcher = mock.patch.object(storage_service.storage, "create_upload_url", return_value="https://s3.test/put")
        self.create_upload_url = patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_upload_returns_signed_url_and_records_file(self):
        response = self.client.post(self.url, upload_body(), format="json")
        self.assertEqual(response.status_code, 200)
        expected_key = f"findthatshit/users/{self.user.id}/resources/{self.resource_id}/resource.pdf"
        self.assertEqual(response.data["s3_object_key"], expected_key)
        self.assertEqual(response.data["upload_url"], "https://s3.test/put")
        self.assertEqual(response.data["method"], "PUT")
        self.assertEqual(response.data["headers"], {"Content-Type": PDF})
        self.assertIn("expires_at", response.data)
        self.create_upload_url.assert_called_once_with(expected_key, PDF, 1234, mock.ANY)
        file = File.objects.get(resource_id=self.resource_id)
        self.assertEqual((file.filename, file.mime_type, file.file_size), ("Modern_Resume_Final.pdf", PDF, 1234))
        self.assertEqual(Resource.objects.get(pk=self.resource_id).resource_type, "PDF")

    def test_all_allowed_types(self):
        cases = [(PDF, "pdf"), (DOCX, "docx"), ("text/plain", "txt")]
        for mime_type, extension in cases:
            response = self.client.post(self.url, upload_body(f"f.{extension}", mime_type), format="json")
            self.assertEqual(response.status_code, 200, mime_type)
            self.assertTrue(response.data["s3_object_key"].endswith(f"/resource.{extension}"))

    def test_unsupported_type_is_rejected_before_presigning(self):
        for mime_type in ("application/x-msdownload", "image/png", "text/html"):
            response = self.client.post(self.url, upload_body("a.exe", mime_type), format="json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["error"]["code"], "UNSUPPORTED_FILE_TYPE")
        self.create_upload_url.assert_not_called()
        self.assertFalse(File.objects.exists())

    def test_oversized_file_is_rejected(self):
        response = self.client.post(self.url, upload_body(size=5001), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "FILE_TOO_LARGE")
        self.assertEqual(self.client.post(self.url, upload_body(size=5000), format="json").status_code, 200)

    def test_invalid_body_is_a_validation_error(self):
        for body in ({}, upload_body(size=0), upload_body(size="big"), upload_body(filename="")):
            response = self.client.post(self.url, body, format="json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")

    def test_unsafe_filenames_are_sanitized(self):
        cases = {"../../etc/passwd.pdf": "passwd.pdf", "C:\\temp\\resume.pdf": "resume.pdf", "..": "resource.pdf"}
        for original, expected in cases.items():
            self.client.post(self.url, upload_body(original), format="json")
            self.assertEqual(File.objects.get(resource_id=self.resource_id).filename, expected)

    def test_requesting_again_replaces_the_file_and_cleans_up_old_object(self):
        self.client.post(self.url, upload_body("a.pdf", PDF), format="json")
        with mock.patch.object(tasks.delete_s3_object, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.url, upload_body("b.txt", "text/plain"), format="json")
        files = File.objects.filter(resource_id=self.resource_id)
        self.assertEqual([f.filename for f in files], ["b.txt"])
        delay.assert_called_once_with(f"findthatshit/users/{self.user.id}/resources/{self.resource_id}/resource.pdf")

    def test_same_extension_reuses_key_without_deleting_it(self):
        self.client.post(self.url, upload_body("a.pdf"), format="json")
        with mock.patch.object(tasks.delete_s3_object, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(self.url, upload_body("b.pdf"), format="json")
        delay.assert_not_called()
        self.assertEqual(File.objects.filter(resource_id=self.resource_id).count(), 1)

    def test_existing_resource_type_is_not_overwritten(self):
        resource_id = self.create_resource(resource_url="https://github.com/a/b")["id"]
        self.client.post(self.detail_url(resource_id, "/upload-url"), upload_body(), format="json")
        self.assertEqual(Resource.objects.get(pk=resource_id).resource_type, "GITHUB")

    def test_storage_failure_is_a_502_and_creates_no_file(self):
        self.create_upload_url.side_effect = StorageProviderError("boom")
        response = self.client.post(self.url, upload_body(), format="json")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["error"]["code"], "STORAGE_ERROR")
        self.assertNotIn("boom", str(response.data))
        self.assertFalse(File.objects.exists())

    def test_upload_url_is_rate_limited(self):
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES", {"upload": "2/min"}):
            codes = [self.client.post(self.url, upload_body(), format="json").status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class DownloadUrlTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.resource_id = self.create_resource()["id"]
        self.url = self.detail_url(self.resource_id, "/download-url")

    def add_file(self):
        File.objects.create(
            resource_id=self.resource_id, s3_object_key="findthatshit/k.pdf", filename="k.pdf",
            mime_type=PDF, file_size=10,
        )

    def test_resource_without_file_returns_404_file_not_found(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "FILE_NOT_FOUND")

    def test_returns_signed_url_when_object_exists(self):
        self.add_file()
        with mock.patch.object(storage_service.storage, "object_exists", return_value=True), mock.patch.object(
            storage_service.storage, "create_download_url", return_value="https://s3.test/get"
        ) as create_download_url:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["url"], "https://s3.test/get")
        self.assertIn("expires_at", response.data)
        create_download_url.assert_called_once_with("findthatshit/k.pdf", "k.pdf", mock.ANY)

    def test_missing_object_is_a_recoverable_conflict(self):
        self.add_file()
        with mock.patch.object(storage_service.storage, "object_exists", return_value=False):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "FILE_NOT_UPLOADED")

    def test_storage_failure_is_a_502(self):
        self.add_file()
        with mock.patch.object(storage_service.storage, "object_exists", side_effect=StorageProviderError("x")):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["error"]["code"], "STORAGE_ERROR")

    def test_detail_and_summary_expose_file_metadata_but_never_the_key(self):
        self.add_file()
        detail = self.client.get(self.detail_url(self.resource_id)).data
        self.assertEqual(detail["resource"]["file"]["filename"], "k.pdf")
        self.assertNotIn("findthatshit/k.pdf", str(detail))
        listing = self.client.get("/api/v1/resources").data["results"][0]
        self.assertTrue(listing["has_file"])
        self.assertNotIn("findthatshit/k.pdf", str(listing))
