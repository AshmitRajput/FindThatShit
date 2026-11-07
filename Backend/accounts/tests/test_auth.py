from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User

PASSWORD = "correct-horse-battery-9"


class AuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def register(self, email="ada@example.com", name="Ada", password=PASSWORD):
        return self.client.post(
            "/api/v1/auth/register",
            {"email": email, "name": name, "password": password},
            format="json",
        )

    def test_register_success_and_me(self):
        response = self.register(" Ada@Example.COM ", " Ada ")
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)
        self.assertTrue(User.objects.filter(email="ada@example.com").exists())
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

    def test_duplicate_email_is_validation_error(self):
        self.register()
        response = self.register("ADA@EXAMPLE.COM")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("email", response.data["error"]["details"]["field_errors"])

    def test_weak_password_is_validation_error(self):
        response = self.register(password="12345678")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data["error"]["details"]["field_errors"])

    def test_empty_registration_has_all_required_errors(self):
        response = self.client.post("/api/v1/auth/register", {}, format="json")
        errors = response.data["error"]["details"]["field_errors"]
        self.assertEqual(response.status_code, 400)
        self.assertTrue({"email", "name", "password"}.issubset(errors))

    def test_login_success(self):
        self.register()
        self.client.post("/api/v1/auth/logout")
        response = self.client.post(
            "/api/v1/auth/login",
            {"email": " ADA@EXAMPLE.COM ", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_wrong_and_unknown_login_are_identical(self):
        self.register()
        self.client.post("/api/v1/auth/logout")
        wrong = self.client.post("/api/v1/auth/login", {"email": "ada@example.com", "password": "wrong"}, format="json")
        unknown = self.client.post("/api/v1/auth/login", {"email": "nobody@example.com", "password": "wrong"}, format="json")
        self.assertEqual(wrong.status_code, unknown.status_code, 401)
        self.assertEqual(wrong.data["error"]["code"], unknown.data["error"]["code"])
        self.assertEqual(wrong.data["error"]["message"], unknown.data["error"]["message"])

    def test_inactive_user_cannot_login(self):
        User.objects.create_user("inactive@example.com", "Inactive", PASSWORD, is_active=False)
        response = self.client.post("/api/v1/auth/login", {"email": "inactive@example.com", "password": PASSWORD}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_logout_then_me_is_unauthenticated(self):
        self.register()
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_logout_while_logged_out_is_unauthenticated(self):
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 401)

    def test_csrf_is_required_for_logout(self):
        client = APIClient(enforce_csrf_checks=True)
        client.post("/api/v1/auth/register", {"email": "a@example.com", "name": "A", "password": PASSWORD}, format="json")
        client.get("/api/v1/auth/me")
        self.assertEqual(client.post("/api/v1/auth/logout").status_code, 403)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_me_requires_authentication_and_request_id(self):
        response = self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "UNAUTHENTICATED")
        self.assertTrue(response.data["error"]["request_id"])
        self.assertEqual(response["WWW-Authenticate"], "Session")

    def test_me_returns_only_user_fields(self):
        self.register()
        response = self.client.get("/api/v1/auth/me")
        self.assertEqual(set(response.data), {"id", "email", "name", "created_at"})

    def test_unknown_route_is_not_found(self):
        response = self.client.get("/api/v1/nope")
        self.assertEqual(response.status_code, 404)
