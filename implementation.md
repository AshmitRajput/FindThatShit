# Implementation Guide

## Feature: Backend foundation (Phase 1)

### Purpose
Runnable Django/DRF backend foundation with PostgreSQL configuration, Redis-backed throttling, Celery wiring, health checks, and session-cookie authentication.

### Logic
- The project contains only the `config`, `accounts`, and `resources` Django apps; `resources` remains a Phase 2 skeleton.
- Settings load from `.env`, use PostgreSQL/Redis/Celery outside tests, and reject unsafe deployed configuration.
- Authentication uses Django sessions. Registration and login create a session; `/api/v1/auth/me` sets the CSRF cookie for the SPA.
- DRF defaults to JSON-only requests, authenticated access, a 401 session authentication header, scoped auth throttling, and one error envelope.
- Every response receives an echoed or generated `X-Request-ID`.

### Files Changed
- `backend/config/`: settings, routing, middleware, health view, exception handling, and Celery application.
- `backend/accounts/`: custom UUID/email user, migration, serializers, session auth views, admin, permissions, and tests.
- `backend/resources/`: empty URLs/model skeleton and `tasks.ping`.
- Root/backend deployment configuration and environment example.

### API / Data Changes
- Added `users` table with UUID primary key, normalized unique email, name, password hash, admin flags, and timestamps.
- Added `GET /api/v1/health/` (also without trailing slash).
- Added `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, and `GET /api/v1/auth/me`.

### Important Notes
- Resource models, uploads, AI, search, tags, voice, and external provider calls are intentionally deferred to Phase 2.
- Docker/PostgreSQL/Redis/Celery live-worker checks were not run.
