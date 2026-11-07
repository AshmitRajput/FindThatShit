import redis
from django.conf import settings
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


def check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


def check_redis():
    try:
        return bool(redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping())
    except Exception:
        return False


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        database_ok = check_database()
        redis_ok = check_redis()
        body = {
            "status": "ok" if database_ok and redis_ok else "degraded",
            "database": "ok" if database_ok else "error",
            "redis": "ok" if redis_ok else "error",
        }
        return Response(body, status=200 if database_ok and redis_ok else 503)