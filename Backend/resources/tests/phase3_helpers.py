import uuid

from django.conf import settings

from resources.models import File, ProcessingJob, Resource, SourceContent

GOOD_ANALYSIS = {
    "title": "Modern Software Engineer Resume",
    "resource_type": "PDF",
    "resource_description": "A resume template for software engineering applications.",
    "source_description": "Reel sharing a free resume template.",
    "summary": "A clean one-page resume template.",
    "keywords": ["resume", "software engineering"],
    "tags": ["Resume", "Career"],
    "language": "en",
}


def make_resource(user, post_text="Free resume templates", **fields):
    resource = Resource.objects.create(
        user=user, platform="INSTAGRAM", source_url="https://www.instagram.com/reel/abc/", **fields
    )
    SourceContent.objects.create(resource=resource, source_type="INSTAGRAM_REEL", post_text=post_text)
    return resource


def make_file(resource, size=5, mime_type="text/plain", key=None):
    return File.objects.create(
        resource=resource, s3_object_key=key or f"findthatshit/test/{resource.pk}.txt",
        filename="a.txt", mime_type=mime_type, file_size=size,
    )


def make_job(resource, **fields):
    return ProcessingJob.objects.create(resource=resource, idempotency_key=uuid.uuid4().hex, **fields)


def vector():
    return [0.1] * settings.EMBEDDING_DIMENSIONS
