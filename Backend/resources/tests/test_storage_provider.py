import os
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from django.test import SimpleTestCase, override_settings

from resources.providers import storage

FAKE_AWS = {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"}


@override_settings(AWS_S3_BUCKET_NAME="test-bucket", AWS_REGION="us-east-1", AWS_S3_ENDPOINT_URL="")
class StorageProviderTests(SimpleTestCase):
    def setUp(self):
        storage._client.cache_clear()
        self.addCleanup(storage._client.cache_clear)
        patcher = mock.patch.dict(os.environ, FAKE_AWS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_upload_url_signs_content_type_and_length(self):
        url = storage.create_upload_url("p/users/u/resources/r/resource.pdf", "application/pdf", 123, 900)
        self.assertIn("test-bucket", url)
        self.assertIn("resource.pdf", url)
        self.assertIn("X-Amz-Expires=900", url)
        signed = url.split("X-Amz-SignedHeaders=")[1].split("&")[0]
        self.assertIn("content-length", signed)
        self.assertIn("content-type", signed)

    def test_download_url_forces_attachment_with_encoded_filename(self):
        url = storage.create_download_url("k.pdf", 'my "file".pdf', 600)
        self.assertIn("response-content-disposition=attachment", url)
        self.assertNotIn('"file"', url)

    def test_missing_bucket_setting_raises(self):
        with override_settings(AWS_S3_BUCKET_NAME=""):
            with self.assertRaises(storage.StorageProviderError):
                storage.create_upload_url("k", "text/plain", 1, 60)

    def test_missing_credentials_raise_provider_error(self):
        client = mock.Mock()
        client.generate_presigned_url.side_effect = NoCredentialsError()
        with mock.patch.object(storage, "_client", return_value=client):
            with self.assertRaises(storage.StorageProviderError):
                storage.create_upload_url("k", "text/plain", 1, 60)

    def test_object_exists_maps_errors(self):
        client = mock.Mock()
        with mock.patch.object(storage, "_client", return_value=client):
            client.head_object.return_value = {}
            self.assertTrue(storage.object_exists("k"))
            client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
            self.assertFalse(storage.object_exists("k"))
            client.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
            with self.assertRaises(storage.StorageProviderError):
                storage.object_exists("k")
            client.head_object.side_effect = EndpointConnectionError(endpoint_url="https://s3")
            with self.assertRaises(storage.StorageProviderError):
                storage.object_exists("k")

    def test_delete_object_wraps_errors(self):
        client = mock.Mock()
        with mock.patch.object(storage, "_client", return_value=client):
            storage.delete_object("k")
            client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="k")
            client.delete_object.side_effect = ClientError({"Error": {"Code": "500"}}, "DeleteObject")
            with self.assertRaises(storage.StorageProviderError):
                storage.delete_object("k")


class DeleteTaskTests(SimpleTestCase):
    def test_task_calls_provider(self):
        from resources import tasks

        with mock.patch.object(tasks, "delete_object") as delete_object:
            tasks.delete_s3_object("some/key")
        delete_object.assert_called_once_with("some/key")

    def test_task_retries_storage_errors_with_backoff(self):
        from resources import tasks

        task = tasks.delete_s3_object
        self.assertEqual(task.autoretry_for, (storage.StorageProviderError,))
        self.assertEqual(task.max_retries, 5)
        self.assertTrue(task.retry_backoff)
