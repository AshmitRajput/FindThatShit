from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient


class HealthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @mock.patch("config.health.check_redis", return_value=True)
    def test_health_accepts_both_route_forms(self, check_redis):
        for path in ("/api/v1/health/", "/api/v1/health"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, {"status": "ok", "database": "ok", "redis": "ok"})

    @mock.patch("config.health.check_redis", return_value=False)
    def test_health_reports_redis_failure(self, check_redis):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["redis"], "error")

    @mock.patch("config.health.check_database", return_value=False)
    @mock.patch("config.health.check_redis", return_value=True)
    def test_health_reports_database_failure(self, check_redis, check_database):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["database"], "error")

    def test_request_id_is_echoed_or_generated(self):
        echoed = self.client.get("/api/v1/health/", HTTP_X_REQUEST_ID="request-123")
        generated = self.client.get("/api/v1/health/")
        self.assertEqual(echoed["X-Request-ID"], "request-123")
        self.assertTrue(generated["X-Request-ID"])
