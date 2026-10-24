# Service SLO Watchdog

A dependency-free external watchdog for services that expose bounded readiness, liveness, revision and dependency metrics.

The tool runs outside the monitored service, verifies five credential-free HTTP endpoints, applies strict response-size and wall-clock limits, and emits only alert/recovery state transitions. Response bodies and exception details never enter alert messages.

## Why

A process-level health check can stay green while a deployment serves the wrong revision, a required worker is down, a database check is slow, or an operational pass route fails. Service SLO Watchdog turns those independent point checks into one bounded external contract.

## Runtime behavior

- standard-library-only runtime;
- exact expected-revision checks across version, readiness and Prometheus metrics;
- configurable service name, metric prefix, dependencies and latency thresholds;
- JSON bodies capped at 16 KiB and metrics capped at 64 KiB;
- `/pass` response body is never read;
- one POSIX wall-clock deadline also interrupts trickled or blocked reads;
- transport errors are reduced to bounded error identifiers;
- alert after a configurable consecutive-failure threshold;
- one recovery message and silence while state is unchanged;
- atomically replaced mode-`0600` state with a private newly-created parent directory;
- report mode for CI/manual inspection and quiet mode for schedulers.

## Monitored contract

See [docs/CONTRACT.md](docs/CONTRACT.md) for the exact endpoint and metric schema.

## Installation from source

Registry publication is intentionally deferred. Clone the repository and install the local project:

```bash
git clone https://github.com/FrankFMY/service-slo-watchdog.git
cd service-slo-watchdog
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

## Report mode

```bash
service-slo-watchdog \
  --base-url https://service.example \
  --service-name orders-api \
  --metric-prefix orders_api \
  --dependencies database,queue \
  --expected-revision abc123 \
  --report
```

Report mode prints bounded JSON and exits nonzero when any SLI fails.

## Quiet scheduler mode

```bash
service-slo-watchdog \
  --base-url https://service.example \
  --service-name orders-api \
  --metric-prefix orders_api \
  --dependencies database,queue \
  --failure-threshold 2 \
  --state-file ~/.local/state/service-slo-watchdog/orders-api.json
```

Ordinary service failures return exit code zero in quiet mode: the state machine owns suppression and recovery delivery. Unexpected watchdog bugs still exit nonzero. Non-empty stdout is suitable for a scheduler/notifier; unchanged healthy or failing state produces no output.

Environment equivalents are available for deployment wrappers:

```text
SERVICE_SLO_BASE_URL
SERVICE_SLO_SERVICE_NAME
SERVICE_SLO_METRIC_PREFIX
SERVICE_SLO_DEPENDENCIES
SERVICE_SLO_EXPECTED_REVISION
SERVICE_SLO_STATE_FILE
```

No service URL, revision or local machine path is built into the package.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable . ruff build twine
python -m unittest discover -s tests -v
python -m compileall -q src tests
python -m build
python -m twine check dist/*
```

## Boundaries

This is a bounded point-probe and state-transition tool. It is not historical SLO accounting, distributed tracing, log storage, an incident-management platform, or a substitute for time-series monitoring. The monitored endpoints must be credential-free and safe to query externally.

The wall-clock deadline requires a POSIX runtime with `setitimer` support.

## Author and contact

**Artem Prianishnikov** · [FrankFMY](https://github.com/FrankFMY)

[Website](https://frankfmy.com) · [Telegram](https://t.me/FrankFMY) · [LinkedIn](https://www.linkedin.com/in/frankfmy/) · [X](https://x.com/FrankFMY) · [npm profile](https://www.npmjs.com/~frankfmy)

Email: [frankfmy@proton.me](mailto:frankfmy@proton.me) · [Pryanishnikovartem@gmail.com](mailto:Pryanishnikovartem@gmail.com)

## License

Apache-2.0 © 2026 Artem Prianishnikov. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
