from unittest import mock

from rest_framework.test import APIClient

from resources import tasks
from resources.models import File, Resource, Tag
from resources.tests.helpers import RESOURCES_URL, ApiTestCase, resource_payload


class CreateResourceTests(ApiTestCase):
    def test_create_minimal_resource(self):
        data = self.create_resource()
        self.assertEqual(data["platform"], "INSTAGRAM")
        self.assertEqual(data["source_type"], "INSTAGRAM_REEL")
        self.assertEqual(data["processing_status"], "UPLOADED")
        self.assertEqual(data["resource_type"], "OTHER")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["tags"], [])
        self.assertFalse(data["has_file"])
        self.assertIn("date_added", data)

    def test_create_full_resource_stores_source_content(self):
        data = self.create_resource(
            source_type="X_POST", creator="@ada", title="Resume pack",
            resource_url="https://www.notion.so/page", tags=["Resume", "templates"],
            post_text="Free resume templates", transcript="hello",
        )
        self.assertEqual(data["platform"], "X")
        self.assertEqual(data["resource_type"], "NOTION")
        self.assertEqual(data["tags"], ["Resume", "templates"])
        detail = self.client.get(self.detail_url(data["id"])).data
        self.assertEqual(detail["source"]["post_text"], "Free resume templates")
        self.assertEqual(detail["source"]["transcript"], "hello")

    def test_resource_type_is_inferred_from_url(self):
        cases = {
            "https://drive.google.com/file/d/1": "GOOGLE_DRIVE",
            "https://github.com/a/b": "GITHUB",
            "https://www.dropbox.com/s/x": "DROPBOX",
            "https://example.com/tool": "WEBSITE",
        }
        for url, expected in cases.items():
            self.assertEqual(self.create_resource(resource_url=url)["resource_type"], expected, url)

    def test_explicit_resource_type_wins(self):
        self.assertEqual(
            self.create_resource(resource_url="https://github.com/a/b", resource_type="PDF")["resource_type"], "PDF"
        )

    def test_tags_are_normalized_and_deduplicated(self):
        data = self.create_resource(tags=["Python", "  python ", "PYTHON", "Interview   Prep"])
        self.assertEqual(data["tags"], ["Interview Prep", "Python"])
        self.assertEqual(Tag.objects.filter(user=self.user).count(), 2)

    def test_same_tag_name_is_reused_not_duplicated(self):
        self.create_resource(tags=["python"])
        self.create_resource(tags=["Python"])
        self.assertEqual(Tag.objects.filter(user=self.user).count(), 1)

    def test_validation_errors(self):
        bad_payloads = [
            {},
            resource_payload(source_type="MYSPACE"),
            resource_payload(source_url="javascript:alert(1)"),
            resource_payload(source_url="ftp://example.com/file"),
            resource_payload(source_url="not a url"),
            resource_payload(resource_url="ftp://example.com/x"),
            resource_payload(resource_type="EXE"),
            resource_payload(tags=[f"t{i}" for i in range(21)]),
            resource_payload(tags=["   "]),
            resource_payload(tags="python"),
        ]
        for payload in bad_payloads:
            response = self.client.post(RESOURCES_URL, payload, format="json")
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")

    def test_ownership_cannot_be_set_from_client_input(self):
        data = self.create_resource(user=str(self.other_user.id), user_id=str(self.other_user.id))
        self.assertEqual(str(Resource.objects.get(pk=data["id"]).user_id), str(self.user.id))

    def test_unauthenticated_requests_are_rejected(self):
        anonymous = APIClient()
        for response in (anonymous.get(RESOURCES_URL), anonymous.post(RESOURCES_URL, resource_payload(), format="json")):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.data["error"]["code"], "UNAUTHENTICATED")


class ListResourceTests(ApiTestCase):
    def test_list_is_scoped_to_the_user(self):
        mine = self.create_resource(title="mine")
        self.create_resource(client=self.other_client, title="theirs")
        response = self.client.get(RESOURCES_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in response.data["results"]], [mine["id"]])
        self.assertEqual(response.data["count"], 1)

    def test_pagination_defaults_limit_and_maximum(self):
        for i in range(25):
            self.create_resource(title=f"r{i:02d}")
        first = self.client.get(RESOURCES_URL).data
        self.assertEqual((first["count"], first["page"], first["limit"], len(first["results"])), (25, 1, 20, 20))
        second = self.client.get(RESOURCES_URL, {"page": 2}).data
        self.assertEqual(len(second["results"]), 5)
        self.assertEqual(self.client.get(RESOURCES_URL, {"limit": 5}).data["limit"], 5)
        self.assertEqual(self.client.get(RESOURCES_URL, {"limit": 500}).data["limit"], 50)
        out_of_range = self.client.get(RESOURCES_URL, {"page": 99})
        self.assertEqual(out_of_range.status_code, 404)
        self.assertEqual(out_of_range.data["error"]["code"], "NOT_FOUND")

    def test_invalid_page_and_limit_are_validation_errors(self):
        for params in ({"limit": "abc"}, {"limit": 0}, {"limit": -5}, {"page": "abc"}, {"page": 0}):
            response = self.client.get(RESOURCES_URL, params)
            self.assertEqual(response.status_code, 400, params)
            self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")
            self.assertIn(next(iter(params)), response.data["error"]["details"]["field_errors"])

    def test_default_sort_is_newest_first_and_title_sort_works(self):
        a = self.create_resource(title="Alpha")
        b = self.create_resource(title="Beta")
        newest_first = [r["id"] for r in self.client.get(RESOURCES_URL).data["results"]]
        self.assertEqual(newest_first, [b["id"], a["id"]])
        by_title = [r["title"] for r in self.client.get(RESOURCES_URL, {"sort": "title"}).data["results"]]
        self.assertEqual(by_title, ["Alpha", "Beta"])

    def test_filters(self):
        self.create_resource(title="reel", source_type="INSTAGRAM_REEL", tags=["resume"], creator="Ada Lovelace")
        self.create_resource(title="xpost", source_type="X_POST", resource_url="https://github.com/a/b")
        titles = lambda params: sorted(r["title"] for r in self.client.get(RESOURCES_URL, params).data["results"])
        self.assertEqual(titles({"platform": "X"}), ["xpost"])
        self.assertEqual(titles({"source_type": "INSTAGRAM_REEL"}), ["reel"])
        self.assertEqual(titles({"resource_type": "GITHUB"}), ["xpost"])
        self.assertEqual(titles({"tag": "RESUME"}), ["reel"])
        self.assertEqual(titles({"creator": "lovelace"}), ["reel"])
        self.assertEqual(titles({"processing_status": "PROCESSED"}), [])

    def test_invalid_filter_value_is_a_validation_error(self):
        response = self.client.get(RESOURCES_URL, {"platform": "MYSPACE"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")

    def test_list_query_count_does_not_grow_with_rows(self):
        for i in range(5):
            self.create_resource(title=f"r{i}", tags=["a", "b"])
        with self.assertNumQueries(4):  # count, resources, tags, files (source_content is joined)
            self.client.get(RESOURCES_URL)


class DetailAndUpdateTests(ApiTestCase):
    def test_detail_shape(self):
        data = self.create_resource(title="T", creator="c", tags=["x"])
        detail = self.client.get(self.detail_url(data["id"])).data
        self.assertEqual(set(detail), {"id", "source", "resource", "ai_metadata", "system"})
        self.assertEqual(detail["resource"]["title"], "T")
        self.assertIsNone(detail["resource"]["file"])
        self.assertEqual(detail["ai_metadata"], {"tags": ["x"], "keywords": []})
        self.assertEqual(detail["system"]["version"], 1)
        self.assertEqual(detail["system"]["processing_status"], "UPLOADED")

    def test_patch_updates_fields_and_bumps_version(self):
        data = self.create_resource(title="Old", tags=["a"])
        response = self.client.patch(
            self.detail_url(data["id"]),
            {"version": 1, "title": "New", "description": "d", "tags": ["b", "c"], "post_text": "pt"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["resource"]["title"], "New")
        self.assertEqual(response.data["resource"]["description"], "d")
        self.assertEqual(response.data["ai_metadata"]["tags"], ["b", "c"])
        self.assertEqual(response.data["source"]["post_text"], "pt")
        self.assertEqual(response.data["system"]["version"], 2)

    def test_patch_with_stale_version_returns_409_and_changes_nothing(self):
        data = self.create_resource(title="Old")
        self.client.patch(self.detail_url(data["id"]), {"version": 1, "title": "First"}, format="json")
        stale = self.client.patch(self.detail_url(data["id"]), {"version": 1, "title": "Second"}, format="json")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["error"]["code"], "VERSION_CONFLICT")
        self.assertEqual(Resource.objects.get(pk=data["id"]).title, "First")

    def test_patch_requires_version_and_a_field(self):
        data = self.create_resource()
        url = self.detail_url(data["id"])
        self.assertEqual(self.client.patch(url, {"title": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(url, {"version": 1}, format="json").status_code, 400)

    def test_patch_cannot_change_immutable_fields(self):
        data = self.create_resource()
        self.client.patch(
            self.detail_url(data["id"]),
            {"version": 1, "title": "x", "processing_status": "PROCESSED", "platform": "X", "user": "x"},
            format="json",
        )
        resource = Resource.objects.get(pk=data["id"])
        self.assertEqual((resource.processing_status, resource.platform), ("UPLOADED", "INSTAGRAM"))

    def test_put_is_not_allowed(self):
        data = self.create_resource()
        response = self.client.put(self.detail_url(data["id"]), {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.data["error"]["code"], "METHOD_NOT_ALLOWED")


class OwnershipTests(ApiTestCase):
    """User B must never see or touch user A's data. Missing and foreign both give 404."""

    def test_other_users_resource_is_hidden_everywhere(self):
        resource_id = self.create_resource()["id"]
        url = self.detail_url(resource_id)
        upload = {"filename": "a.pdf", "mime_type": "application/pdf", "size": 10}
        responses = [
            self.other_client.get(url),
            self.other_client.patch(url, {"version": 1, "title": "hacked"}, format="json"),
            self.other_client.delete(url),
            self.other_client.post(url + "/upload-url", upload, format="json"),
            self.other_client.get(url + "/download-url"),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.data["error"]["code"], "NOT_FOUND")
        self.assertEqual(Resource.objects.get(pk=resource_id).title, "")

    def test_unknown_and_malformed_ids(self):
        self.assertEqual(self.client.get(self.detail_url("11111111-1111-1111-1111-111111111111")).status_code, 404)
        malformed = self.client.get(self.detail_url("not-a-uuid"))
        self.assertEqual(malformed.status_code, 404)
        self.assertEqual(malformed.json()["error"]["code"], "NOT_FOUND")

    def test_tags_are_private_per_user(self):
        self.create_resource(tags=["secret"])
        self.create_resource(client=self.other_client, tags=["secret"])
        self.assertEqual(Tag.objects.filter(normalized_name="secret").count(), 2)
        self.assertEqual(self.other_client.get(RESOURCES_URL, {"tag": "secret"}).data["count"], 1)


class DeleteResourceTests(ApiTestCase):
    def test_delete_removes_resource(self):
        resource_id = self.create_resource()["id"]
        self.assertEqual(self.client.delete(self.detail_url(resource_id)).status_code, 204)
        self.assertEqual(self.client.get(self.detail_url(resource_id)).status_code, 404)
        self.assertFalse(Resource.objects.filter(pk=resource_id).exists())

    def test_delete_schedules_s3_cleanup_after_commit(self):
        resource_id = self.create_resource()["id"]
        File.objects.create(
            resource_id=resource_id, s3_object_key="findthatshit/k.pdf", filename="k.pdf",
            mime_type="application/pdf", file_size=1,
        )
        with mock.patch.object(tasks.delete_s3_object, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.delete(self.detail_url(resource_id)).status_code, 204)
        delay.assert_called_once_with("findthatshit/k.pdf")

    def test_delete_succeeds_even_if_enqueue_fails(self):
        resource_id = self.create_resource()["id"]
        File.objects.create(
            resource_id=resource_id, s3_object_key="k2", filename="k", mime_type="text/plain", file_size=1
        )
        with mock.patch.object(tasks.delete_s3_object, "delay", side_effect=RuntimeError("broker down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.delete(self.detail_url(resource_id)).status_code, 204)
