from unittest import mock

from rest_framework.test import APIClient

from resources import tasks
from resources.models import IdempotencyKey, ProcessingJob, Resource
from resources.services import idempotency_service
from resources.tests.helpers import RESOURCES_URL, ApiTestCase, make_user, resource_payload


def key(value):
    return {"HTTP_IDEMPOTENCY_KEY": value}


class IdempotentCreateTests(ApiTestCase):
    def post(self, payload=None, client=None, **headers):
        return (client or self.client).post(RESOURCES_URL, payload or resource_payload(), format="json", **headers)

    def test_same_key_and_payload_replays_the_first_result(self):
        first, second = self.post(**key("k1")), self.post(**key("k1"))
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Resource.objects.count(), 1)

    def test_same_key_with_different_payload_is_a_conflict(self):
        self.post(**key("k1"))
        response = self.post(resource_payload(creator="someone else"), **key("k1"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(Resource.objects.count(), 1)

    def test_key_longer_than_64_characters_is_rejected(self):
        response = self.post(**key("x" * 65))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(Resource.objects.count(), 0)

    def test_without_a_key_every_request_creates_a_resource(self):
        self.post()
        self.post()
        self.assertEqual(Resource.objects.count(), 2)
        self.assertEqual(IdempotencyKey.objects.count(), 0)

    def test_keys_are_independent_per_user(self):
        mine, theirs = self.post(**key("k1")), self.post(client=self.other_client, **key("k1"))
        self.assertEqual((mine.status_code, theirs.status_code), (201, 201))
        self.assertNotEqual(mine.data["id"], theirs.data["id"])

    def test_replay_after_the_resource_was_deleted_is_not_found(self):
        created = self.post(**key("k1"))
        self.client.delete(self.detail_url(created.data["id"]))
        response = self.post(**key("k1"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_equivalent_payloads_have_the_same_fingerprint(self):
        self.assertEqual(
            idempotency_service.fingerprint({"a": 1, "b": 2}), idempotency_service.fingerprint({"b": 2, "a": 1})
        )
        self.assertNotEqual(idempotency_service.fingerprint({"a": 1}), idempotency_service.fingerprint({"a": 2}))


class IdempotentProcessTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(tasks.process_resource, "delay")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.resource_id = self.create_resource(post_text="text")["id"]
        self.url = self.detail_url(self.resource_id, "/process")

    def test_same_key_returns_the_same_job(self):
        first = self.client.post(self.url, {}, format="json", **key("p1"))
        second = self.client.post(self.url, {}, format="json", **key("p1"))
        self.assertEqual((first.status_code, second.status_code), (202, 202))
        self.assertEqual(first.data["job_id"], second.data["job_id"])
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_same_key_with_different_force_value_is_a_conflict(self):
        self.client.post(self.url, {}, format="json", **key("p1"))
        response = self.client.post(self.url, {"force": True}, format="json", **key("p1"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "IDEMPOTENCY_CONFLICT")


class IdempotencyServiceTests(ApiTestCase):
    def test_failed_work_does_not_consume_the_key(self):
        def perform():
            raise RuntimeError("work failed")

        with self.assertRaises(RuntimeError):
            idempotency_service.run(self.user, "k1", "POST /x", "hash", perform)
        self.assertEqual(IdempotencyKey.objects.count(), 0)
        replayed, status, _, _ = idempotency_service.run(self.user, "k1", "POST /x", "hash", lambda: (201, None, None))
        self.assertEqual((replayed, status), (False, 201))
