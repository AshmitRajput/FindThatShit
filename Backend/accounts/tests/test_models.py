from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User


class UserModelTests(TestCase):
    def test_create_user_normalizes_and_hashes(self):
        user = User.objects.create_user(" Ada@Example.COM ", " Ada ", "correct-horse-battery-9")
        self.assertEqual(user.email, "ada@example.com")
        self.assertEqual(user.name, "Ada")
        self.assertTrue(user.check_password("correct-horse-battery-9"))
        self.assertNotEqual(user.password, "correct-horse-battery-9")
        self.assertIsNotNone(user.id)

    def test_duplicate_email_raises_integrity_error(self):
        User.objects.create_user("ada@example.com", "Ada", "correct-horse-battery-9")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user("ada@example.com", "Another", "correct-horse-battery-9")

    def test_create_superuser_sets_admin_flags(self):
        user = User.objects.create_superuser("admin@example.com", "Admin", "correct-horse-battery-9")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
