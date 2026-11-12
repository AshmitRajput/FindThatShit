# Implementation Guide

## Feature: Backend foundation (Phase 1)

### Purpose
Runnable Django/DRF backend foundation with PostgreSQL configuration, Redis-backed throttling, Celery wiring, health checks, and session-cookie authentication.

### Logic
- The project contains only the `config`, `accounts`, and `resources` Django apps.
- Settings load from `.env`, use PostgreSQL/Redis/Celery outside tests, and reject unsafe deployed configuration.
- Authentication uses Django sessions. Registration and login create a session; `/api/v1/auth/me` sets the CSRF cookie for the SPA.
- DRF defaults to JSON-only requests, authenticated access, a 401 session authentication header, scoped auth throttling, and one error envelope.
- Every response receives an echoed or generated `X-Request-ID`.

### Files Changed
- `backend/config/`: settings, routing, middleware, health view, exception handling, and Celery application.
- `backend/accounts/`: custom UUID/email user, migration, serializers, session auth views, admin, permissions, and tests.

### API / Data Changes
- `users` table (UUID pk, normalized unique email, name, password hash, admin flags, timestamps).
- `GET /api/v1/health/` (also without trailing slash); `POST /api/v1/auth/register|login|logout`; `GET /api/v1/auth/me`.

### Important Notes
- Auth transport decision: session cookie + CSRF header (`X-CSRFToken`).

---

## Feature: Resource library (Phase 2)

### Purpose
Private per-user library of saved resources (URL and/or file) with source context and tags. No AI processing yet.

### Logic
- `ResourceViewSet` (thin) calls `resources/services/resource_service.py`. All reads go through `base_queryset(user)`, which is user-scoped and prefetches tags/files; other users' resources return 404.
- Create writes `Resource` + `SourceContent` + tags in one transaction. `platform` is derived from `source_type`; `resource_type` is the client value or inferred from the URL host.
- PATCH uses optimistic locking: conditional UPDATE on `version`, then `version + 1`; a stale version returns 409 `VERSION_CONFLICT`. Only title, creator, source_url, resource_url, resource_type, description, tags, post_text, transcript can change.
- Tags are per-user, case-insensitive, de-duplicated; a PATCH `tags` list replaces the existing tags.
- List supports filters (platform, source_type, resource_type, processing_status, tag, creator), sorting, and pagination (`page`, `limit`; default 20, max 50) with a constant query count.
- Delete cascades to source content, files and tag links; S3 cleanup is queued after commit.

### Files Changed
- `backend/resources/`: `models.py`, `migrations/0001_initial.py`, `serializers.py`, `views.py`, `urls.py`, `admin.py`, `exceptions.py`, `validators.py`, `services/resource_service.py`, `tests/`.
- `backend/config/`: `pagination.py`, `views.py` (JSON 404), `exceptions.py` (`APIError`), `settings.py`, `urls.py`.

### API / Data Changes
- Tables: `resources`, `source_contents`, `files`, `tags`, `resource_tags` (UUID pks except `resource_tags`; CHECK constraints on enum columns; unique `(user, normalized_name)` on tags).
- Endpoints: `POST/GET /api/v1/resources`, `GET/PATCH/DELETE /api/v1/resources/{id}`.

### Important Notes
- `keywords` is a JSON list (portable) and `date_added` is `created_at`. Optional text fields store `""`.
- `processing_status` cannot be set by clients; Phase 3 changes it through a service.
- Idempotency keys are deferred to Phase 3.

---

## Feature: File storage (Phase 2)

### Purpose
Upload documents directly to private S3 and download them securely.

### Logic
- `POST /resources/{id}/upload-url` validates MIME type (PDF/DOCX/TXT) and size (`MAX_UPLOAD_SIZE_BYTES`), builds the key `findthatshit/users/{user_id}/resources/{resource_id}/resource.{ext}` (extension from MIME, never from the filename), presigns a PUT that signs `Content-Type` and `Content-Length`, then replaces the resource's single `File` row. An old object with a different key is deleted by the `delete_s3_object` Celery task after commit.
- `GET /resources/{id}/download-url` checks the object exists (HEAD) and returns a time-limited presigned GET with an attachment disposition.
- Only `resources/providers/storage.py` imports boto3; failures become `StorageProviderError`, then a 502 `STORAGE_ERROR` without internal details.

### Files Changed
- `backend/resources/providers/storage.py`, `services/storage_service.py`, `tasks.py` (`delete_s3_object`), `views.py`, `serializers.py`.
- `backend/scripts/smoke_phase2.py` (manual end-to-end check), `requirements.txt` (`boto3`), `.env.example`.

### API / Data Changes
- Endpoints: `POST /api/v1/resources/{id}/upload-url` (rate limited, `upload` scope), `GET /api/v1/resources/{id}/download-url`.
- Error codes: `UNSUPPORTED_FILE_TYPE`, `FILE_TOO_LARGE`, `FILE_NOT_FOUND`, `FILE_NOT_UPLOADED`, `STORAGE_ERROR`.
- Settings: `AWS_S3_BUCKET_NAME`, `AWS_REGION`, `AWS_S3_KEY_PREFIX`, `AWS_S3_ENDPOINT_URL`, `S3_PRESIGN_UPLOAD_TTL_SECONDS`, `S3_PRESIGN_DOWNLOAD_TTL_SECONDS`, `MAX_UPLOAD_SIZE_BYTES`, `UPLOAD_THROTTLE_RATE`.

### Important Notes
- The bucket must stay private and allow CORS `PUT/GET/HEAD` from the web origin. The IAM policy needs `s3:ListBucket`, otherwise a missing object looks like 403 and surfaces as `STORAGE_ERROR`.
- The S3 key is never returned in list/detail responses.
- Live S3 and Redis behaviour is verified with `python scripts/smoke_phase2.py`; unit tests mock the storage provider.

### Phase 2 Definition of Done
- Resource, source-content, file, tag, and resource-tag models and generated migration are present.
- User-scoped CRUD, tag normalization/replacement, pagination, filtering, sorting, optimistic locking, and JSON 404 responses are implemented.
- Upload URLs validate PDF/DOCX/TXT and size limits, sanitize filenames, use MIME-derived S3 keys, replace the single file row, and schedule old-object cleanup.
- Download URLs distinguish missing file metadata, not-yet-uploaded objects, and storage failures.
- Search, voice, and extra Django apps remain out of scope for Phase 2.

---

## Feature: Async processing (Phase 3)

### Purpose
Process uploaded documents and supplied source text asynchronously, persist validated AI metadata, and store embeddings.

### Logic
- `POST /api/v1/resources/{id}/process` creates or reuses one active `ProcessingJob`, marks the resource `PROCESSING`, and enqueues Celery after commit.
- Workers claim pending or stale jobs with row locking, extract PDF/DOCX/TXT content, call the provider adapter, save AI metadata before embedding, and finish as `PROCESSED` or `FAILED`.
- Retryable provider and storage failures use Celery retries; provider responses and document contents are never logged or returned.
- `GET /api/v1/resources/{id}/processing` returns the latest job state. Ownership remains enforced through the resource queryset.

### Files Changed
- `backend/resources/models.py`, migration `0002_*`, serializers, views, admin, tasks.
- `backend/resources/providers/ai.py`, `providers/storage.py`, `extractors/documents.py`.
- `backend/resources/services/analysis_schema.py`, `content_service.py`, `processing_service.py`.

### API / Data Changes
- Added `ProcessingJob`, `Embedding`, `resources.summary`, and `resources.content_language`.
- Added processing stages/statuses and errors `ALREADY_PROCESSED` and `NOTHING_TO_PROCESS`.
- Embeddings use configured `EMBEDDING_DIMENSIONS` (768 default) with a SQLite-compatible test field.

### Important Notes
- Phase 3 does not fetch external URLs or call STT/TTS; URL/post/transcript metadata is processed as supplied.
- The embedding model and provider URLs are configured through environment variables.

## Feature: Idempotency (Phase 3)

### Purpose
Make resource creation and processing requests safe to replay.

### Logic
- Optional `Idempotency-Key` values are limited to 64 characters and are scoped by user and endpoint.
- A matching key and payload replays the original identifiers; a different payload returns `IDEMPOTENCY_CONFLICT`.

### Files Changed
- `backend/resources/models.py`, `migrations/0002_*`, `services/idempotency_service.py`, and `views.py`.

### API / Data Changes
- Added the `idempotency_keys` table and `Idempotency-Key` CORS header.
- `POST /api/v1/resources` and `/process` now support idempotent replay.

### Important Notes
- The idempotency record and database work are transactional; external S3 and AI calls remain outside transactions.