# Reliability

## Redis outage

Single and batch evaluation fall back to eager PostgreSQL loads when Redis reads fail. Cache writes and invalidations are best effort and logged. Readiness reports `degraded` with HTTP 200 while PostgreSQL remains available because the core evaluation contract still works. Rate limiting falls back to bounded local counters, and SSE tells connected clients to refetch a snapshot if Pub/Sub fails.

## PostgreSQL outage

Uncached configuration cannot be recovered authoritatively, management writes cannot commit, and readiness returns HTTP 503. A still-cached flag may technically exist in Redis, but CohortSwitch deliberately does not promise cache-only operation because stable cache entries expire and Redis is not authoritative.

## Stale cache

Commit-then-invalidate has a failure window if a writer dies after commit. Short TTL bounds the stale period. Cached payloads expose their flag version, SSE messages expose the new version and environment revision, and snapshot consumers can compare ETags.

## Concurrent updates

The conditional version update admits one writer for a given expected version. Child replacement, snapshot creation, revision increment, and audit append roll back together if any part fails. The API returns the winning current version to the stale writer.

## SSE disconnect

Pub/Sub events are not durable. Clients reconnect with backoff and fetch the environment snapshot. The ETag revision returns 304 when no change occurred; otherwise the snapshot fully repairs client state.

## SDK timeout and fallback

The SDK has explicit HTTP timeouts and typed errors. A caller may enable a short in-process decision cache; fallback values remain an application policy because the correct safe default is domain-specific. The SDK never silently invents a flag value after an API failure.

