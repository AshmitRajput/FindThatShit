from django.db import IntegrityError, transaction
from django.test import TestCase

from resources.models import File, Resource, ResourceTag, SourceContent, Tag
from resources.tests.helpers import make_user


class ModelConstraintTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.resource = Resource.objects.create(
            user=self.user, platform="INSTAGRAM", source_url="https://example.com/p/1"
        )

    def test_defaults(self):
        self.assertEqual(self.resource.processing_status, "UPLOADED")
        self.assertEqual(self.resource.resource_type, "OTHER")
        self.assertEqual(self.resource.version, 1)
        self.assertEqual(self.resource.keywords, [])

    def test_invalid_enum_value_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Resource.objects.create(user=self.user, platform="MYSPACE", source_url="https://example.com")

    def test_tag_unique_per_user_but_not_across_users(self):
        Tag.objects.create(user=self.user, name="Python", normalized_name="python")
        Tag.objects.create(user=make_user("b@example.com"), name="Python", normalized_name="python")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Tag.objects.create(user=self.user, name="PYTHON", normalized_name="python")

    def test_resource_tag_pair_unique(self):
        tag = Tag.objects.create(user=self.user, name="a", normalized_name="a")
        ResourceTag.objects.create(resource=self.resource, tag=tag)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ResourceTag.objects.create(resource=self.resource, tag=tag)

    def test_file_object_key_unique(self):
        kwargs = dict(s3_object_key="k/1", filename="a.pdf", mime_type="application/pdf", file_size=1)
        File.objects.create(resource=self.resource, **kwargs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            File.objects.create(resource=self.resource, **kwargs)

    def test_source_content_is_one_to_one(self):
        SourceContent.objects.create(resource=self.resource, source_type="OTHER")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SourceContent.objects.create(resource=self.resource, source_type="OTHER")

    def test_deleting_resource_cascades(self):
        SourceContent.objects.create(resource=self.resource, source_type="OTHER")
        File.objects.create(resource=self.resource, s3_object_key="k/2", filename="a", mime_type="text/plain", file_size=1)
        self.resource.delete()
        self.assertEqual(SourceContent.objects.count(), 0)
        self.assertEqual(File.objects.count(), 0)
