"""Transactional Idempotency-Key support."""
import hashlib
import json

from django.db import IntegrityError, transaction
from rest_framework import serializers

from resources.exceptions import IdempotencyConflictError
from resources.models import IdempotencyKey

MAX_KEY_LENGTH = 64


def get_key(request):
    key = request.META.get("HTTP_IDEMPOTENCY_KEY", "").strip()
    if len(key) > MAX_KEY_LENGTH:
        raise serializers.ValidationError(
            {"idempotency_key": [f"Must be at most {MAX_KEY_LENGTH} characters."]}
        )
    return key or None


def fingerprint(payload):
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@transaction.atomic
def run(user, key, endpoint, request_hash, perform):
    if key is None:
        return False, *perform()
    try:
        with transaction.atomic():
            record = IdempotencyKey.objects.create(
                user=user, key=key, endpoint=endpoint, request_hash=request_hash
            )
    except IntegrityError:
        record = IdempotencyKey.objects.get(user=user, key=key, endpoint=endpoint)
        if record.request_hash != request_hash:
            raise IdempotencyConflictError()
        return True, record.response_status, record.resource_id, record.job_id
    response_status, resource_id, job_id = perform()
    IdempotencyKey.objects.filter(pk=record.pk).update(
        response_status=response_status, resource_id=resource_id, job_id=job_id
    )
    return False, response_status, resource_id, job_id
