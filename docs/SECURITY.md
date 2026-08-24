# Security

## Authentication

User passwords are hashed with Argon2. Access tokens are short-lived signed JWTs. Refresh tokens are opaque random values stored only as SHA-256 hashes; every refresh rotates the token, and reuse revokes other active refresh tokens for the user.

SDK keys use the `cs_sdk_live_` prefix and are bound to one project and environment. Management keys use `cs_api_live_` and carry explicit scopes. Only a display prefix and SHA-256 hash are stored; the raw key is returned once.

## Authorization and tenant isolation

Organization roles map to a centralized permission set. Resource authorization walks from environment or project to its owning organization before returning the resource. SDK evaluation derives project and environment from the authenticated key rather than request-supplied tenant identifiers.

An SDK key contains only `flags:read` and `evaluate`. It cannot create or mutate flags, enumerate audit events, or manage keys.

## Logging and audit

Request logs contain request ID, method, normalized route, status, and duration. They exclude passwords, tokens, API keys, subject keys, and attribute maps. Audit metadata is allow-listed at each write and never contains raw credentials or variations with user context.

## Rate limiting

Redis provides per-minute evaluation and management buckets. If Redis fails, a bounded process-local limiter preserves basic abuse protection and endpoint availability. The fallback is intentionally approximate across replicas and emits a warning.

## Secret management

Production settings reject the development JWT secret. Secrets come from environment variables and `.env` is ignored. CORS origins, trusted hosts, body size, token TTLs, cache TTL, and rate limits are configurable.

## Input safety

Pydantic validates JSON shapes, lengths, enum values, timestamps, and rollout boundaries. SQLAlchemy emits parameterized SQL. A body-size middleware rejects declared oversized payloads. Error handlers return stable envelopes without tracebacks.

