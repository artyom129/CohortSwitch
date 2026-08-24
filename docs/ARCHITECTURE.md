# Architecture

## Request flow

Management requests authenticate a user JWT or scoped management key, resolve organization membership, and apply a centralized permission matrix before accessing a project resource. Evaluation requests authenticate an environment-scoped SDK or management key and do not accept tenant IDs from the request body.

Every request receives a UUID request ID. HTTP logs use the normalized route template and exclude credentials and subject attributes.

## Evaluation flow

The evaluation path asks Redis for a compiled `FlagConfiguration`. A miss loads one normalized configuration from PostgreSQL with eager relationships, validates it through the pure engine model, and stores the compiled JSON with a short TTL. Batch evaluation uses `MGET` and one eager PostgreSQL query for all misses.

Redis failures are caught at the cache boundary. PostgreSQL remains the authoritative fallback.

## Data model

Organizations own projects; membership rows bind users to organizations and roles. Projects own environments and feature flags. A `FlagConfig` joins a flag to an environment and owns variations, rules, conditions, rollout allocations, progressive stages, immutable versions, and scheduled changes.

The flag key is unique inside a project and immutable through the API. Production deletion is a soft archive. Archived flags remain in versions and audit history but disappear from evaluation and exported bundles.

## PostgreSQL and Redis responsibilities

PostgreSQL stores users, token hashes, tenant structure, normalized flag configuration, versions, scheduled changes, API key hashes, and audit events. All durable invariants use foreign keys, unique constraints, check constraints, or atomic conditional updates.

Redis stores recoverable compiled JSON, rate-limit counters, and transient Pub/Sub messages. Losing Redis degrades latency, rate-limit precision, and real-time notifications but does not erase or redefine configuration.

## Consistency model

A configuration write performs the optimistic version update, replaces normalized children, appends `FlagVersion`, increments `Environment.revision`, and appends `AuditEvent` inside one PostgreSQL transaction. The losing concurrent writer observes no matching `current_version` row and receives `409` with the current version.

Cache invalidation occurs after commit. There is no dual write inside the database transaction.

## Cache strategy

Stable per-flag keys make lookup possible without first querying a version pointer. Cached payloads include `config_version`; the control plane deletes the stable key after commit and publishes the new version and environment revision.

The failure window is writer commit followed by process failure before deletion. A 30-second default TTL bounds that stale read. Versioned keys plus an authoritative pointer would still require invalidating or updating the pointer; a transactional outbox would narrow the window but add a delivery subsystem disproportionate to this project. CohortSwitch chooses explicit bounded staleness, observability, and PostgreSQL fallback.

## Versioning and rollback

Snapshots contain the entire engine input, including salt, variations, rules, allocation boundaries, and progressive stages. Rollback validates the old snapshot and applies it through the normal write transaction with the current `expected_version`. The new version records `source_version`.

## Rollout hashing

BLAKE2b hashes length-prefixed UTF-8 tuple components and maps an unsigned 64-bit prefix modulo 10,000. Length prefixes avoid tuple ambiguity, the per-config salt isolates unrelated flags, and cumulative bucket boundaries preserve cohorts as a percentage grows.

## Concurrency handling

Flag writers use atomic compare-and-swap in PostgreSQL. Scheduled workers claim due rows with `FOR UPDATE SKIP LOCKED`, so several scheduler instances can run without applying one change twice. Environment revision increments in the same transaction provide a coarse configuration generation for ETags and SSE recovery.

