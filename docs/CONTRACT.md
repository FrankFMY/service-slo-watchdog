# Monitored Service Contract

Service SLO Watchdog performs sequential GET requests to five root endpoints. A unique `probe` query parameter prevents intermediary cache reuse.

## `/version`

HTTP 200 with a bounded JSON object:

```json
{"revision":"abc123"}
```

`revision` must be a non-empty portable identifier. When `--expected-revision` is supplied, the values must match exactly.

## `/ready`

HTTP 200:

```json
{"ok":true,"status":"ready","revision":"abc123"}
```

The readiness revision must equal `/version`.

## `/health`

HTTP 200:

```json
{"ok":true,"status":"alive"}
```

## `/metrics`

HTTP 200 with exact Prometheus text lines derived from `--metric-prefix` and `--dependencies`:

```text
orders_api_release_info{revision="abc123"} 1
orders_api_dependency_up{dependency="database"} 1
orders_api_dependency_up{dependency="queue"} 1
orders_api_dependency_check_duration_seconds{dependency="database"} 0.010
orders_api_dependency_check_duration_seconds{dependency="queue"} 0.020
```

Only declared dependencies and the declared metric namespace are accepted. Scientific notation is supported for finite numeric values. A dependency is healthy only when its `up` value is exactly `1` and its duration is within the configured dependency SLO.

Every declared release, dependency-up and dependency-duration series must occur exactly once. Duplicate samples fail closed with a bounded `metrics_duplicate:*` error; later values never overwrite the first sample.

## `/pass`

The status must be HTTP 200. The response body is deliberately not read.

## Error vocabulary

Errors contain identifiers, not bodies or exception strings. Examples:

```text
revision_mismatch
ready_not_ok
metrics_revision_missing
metrics_duplicate:release
metrics_duplicate:dependency_up:queue
metrics_duplicate:dependency_duration:database
dependency_down:queue
dependency_slow:database
request_failed:metrics
request_slow:ready
pass_http_status:503
```

Unknown or malformed externally supplied error values are replaced with `invalid_error` before alert rendering.
