from urllib.parse import urlparse

from rest_framework import serializers


def validate_http_url(value):
    if urlparse(value).scheme.lower() not in {"http", "https"}:
        raise serializers.ValidationError("Only http and https URLs are allowed.")
