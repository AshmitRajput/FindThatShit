from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User

PASSWORD = "correct-horse-battery-9"
RESOURCES_URL = "/api/v1/resources"


def make_user(email="ada@example.com", name="Ada"):
    return User.objects.create_user(email=email, name=name, password=PASSWORD)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def resource_payload(**overrides):
    data = {"source_type": "INSTAGRAM_REEL", "source_url": "https://www.instagram.com/reel/abc/"}
    data.update(overrides)
    return data


class ApiTestCase(TestCase):
    """Two users, one client each. Clears the cache so throttle counters reset."""

    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.other_user = make_user("grace@example.com", "Grace")
        self.client = client_for(self.user)
        self.other_client = client_for(self.other_user)

    def create_resource(self, client=None, **overrides):
        response = (client or self.client).post(RESOURCES_URL, resource_payload(**overrides), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def detail_url(self, resource_id, suffix=""):
        return f"{RESOURCES_URL}/{resource_id}{suffix}"
