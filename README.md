# Boolean flag evaluator (ralph-canary)

A local draft-PR MVP for global boolean flags. PostgreSQL is the authority for current state and append-only history; Redis is an optional disposable read-through cache. A small browser operator panel is available at `http://localhost:8000/admin`.

## Run locally with Docker Compose

Requirements: Docker Engine with the Compose plugin.

```sh
cp .env.example .env
# Replace the local sample secrets/tokens before sharing the service.
docker compose up --build
```

Compose starts PostgreSQL 16, Redis 7, and the API. PostgreSQL initialization creates the tables and a restricted `flag_app` role; its grants allow current-state writes and history inserts but not history updates or deletes. The database volume is named `flag-data`. To remove the local data explicitly, use `docker compose down -v`.

Open the admin panel and paste an authorized bearer token from `FLAG_AUTH_TOKENS`. The token remains in page memory and is not stored in a cookie or local storage. The included sample tokens are local-only. The API also accepts bearer tokens directly. Set `REDIS_URL` to an empty value to disable caching; the evaluator continues to use PostgreSQL.

`FLAG_AUTH_TOKENS` is a JSON array of objects with a unique `token`, `actor_id`, and role (`reader`, `auditor`, or `operator`). Tokens must be at least 16 characters. Missing or malformed configuration denies access. Role capabilities are deliberately separate: readers can evaluate and list; auditors can also read history; operators can evaluate, list, create, and update.

## API

All routes use JSON and return errors in `{ "code", "message", "request_id" }` form. Every request gets an `X-Request-ID` UUID; clients may supply a UUID in that header. Internal database/cache details are not returned.

| Route | Capability | Behavior |
| --- | --- | --- |
| `GET /v1/flags/{flag_key}` | reader, auditor, operator | Evaluate one flag; unknown key is `404 FLAG_NOT_FOUND`. |
| `GET /v1/admin/flags?limit=50&cursor=…` | reader, auditor, operator | Key-ordered page; limit 1–200; opaque cursor. |
| `POST /v1/admin/flags` | operator | Body `{ "flag_key": "checkout.new_flow", "value": true }`; returns `201` and `ETag: "1"`. |
| `PUT /v1/admin/flags/{flag_key}` | operator | Body `{ "value": false }`; requires `If-Match: "<version>"`. |
| `GET /v1/admin/flags/{flag_key}/history?limit=50&cursor=…` | auditor | Newest-first immutable history page. |

Example:

```sh
curl -H 'Authorization: Bearer local-operator-token-change-me' \
  -H 'Content-Type: application/json' \
  -d '{"flag_key":"checkout.new_flow","value":true}' \
  http://localhost:8000/v1/admin/flags

curl -H 'Authorization: Bearer local-reader-token-change-me' \
  http://localhost:8000/v1/flags/checkout.new_flow
```

Flag keys are 1–128 ASCII characters matching `^[a-z][a-z0-9_.-]{0,127}$`. Write bodies reject unknown fields; `value` must be a JSON boolean. Keys are bound as values in parameterized database operations. Requests over 16 KiB are rejected. There is no delete route. Versions are decimal strings from 1 through signed PostgreSQL `BIGINT` maximum; a stale update returns `412 VERSION_MISMATCH` and `current_version`, a missing `If-Match` is `428 PRECONDITION_REQUIRED`, and exhaustion is `409 VERSION_EXHAUSTED`. Same-value updates still increment the version and create history.

## Persistence, cache, and failure semantics

Creation of the current record and version-1 history row is transactional. Updates conditionally match the requested version, increment once, and append the corresponding history row in the same transaction. Unique constraints enforce one key and one history entry per flag/version. Database timestamps are UTC-capable `TIMESTAMPTZ`. History records actor ID, request ID, key snapshot, value, version, operation, and timestamp. Failed writes create no history. The app database role and a database trigger prohibit history updates/deletes. There is no automatic history pruning; a local database reset removes current flags and history together.

Redis uses `ralph-canary:flag:v1:<flag_key>` and caches found values only. Successful populations use a fixed 300-second TTL; reads do not refresh it. Cache errors and malformed entries fall back to PostgreSQL. After a committed write, eviction is attempted best-effort; eviction failure does not change the committed response. No outbox, retry worker, or pending-invalidation response is used. A missed/failed eviction can expose a cached old version until the entry's remaining TTL ends. A Redis outage uses PostgreSQL. With PostgreSQL unavailable, a still-readable cache hit can serve its snapshot; misses and unusable cache data return `503 SERVICE_UNAVAILABLE`. Cache hits do not prove freshness. Missing flags are not cached or confused with backend failures.

Authentication is deny-by-default and performed for each request. API auth uses bearer headers rather than cookie sessions, so browser mutations do not depend on ambient cookies; the panel does not persist tokens. Admin reads and actions are attributable in application logs; successful writes have durable actor/request attribution in history.

Administrative creates and updates are limited to 60 per actor per minute per API process by default. Set `FLAG_WRITE_LIMIT_PER_MINUTE` to adjust the local MVP limit. This small in-process limit is not a distributed or production rate-limiting service.

## Tests and checks

Install the development dependencies and run focused tests:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
docker compose config
```

The automated suite exercises API authorization and validation, version preconditions/exhaustion, history and attribution, cursor bounds, cache TTL/read-through/fallback, and post-commit best-effort eviction. It is a local correctness suite, not a load test, production capacity plan, deployment approval, or migration program.
