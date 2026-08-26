from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Sequence
from pathlib import Path

from .watchdog import ProbeContract, probe, update_alert_state


def parse_dependencies(value: str) -> tuple[str, ...]:
    raw = value.split(",")
    if not raw or any(not item.strip() for item in raw):
        raise ValueError("dependencies must be a non-empty comma-separated list")
    dependencies = tuple(item.strip() for item in raw)
    if len(set(dependencies)) != len(dependencies):
        raise ValueError("dependencies must be unique")
    return dependencies


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be finite and positive")
    return parsed


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="service-slo-watchdog",
        description="Bounded external SLO probes with deduplicated alert and recovery state.",
    )
    parser.add_argument("--base-url", default=os.environ.get("SERVICE_SLO_BASE_URL"))
    parser.add_argument("--service-name", default=os.environ.get("SERVICE_SLO_SERVICE_NAME", "service"))
    parser.add_argument("--metric-prefix", default=os.environ.get("SERVICE_SLO_METRIC_PREFIX"))
    parser.add_argument("--dependencies", default=os.environ.get("SERVICE_SLO_DEPENDENCIES", "database,worker"))
    parser.add_argument("--expected-revision", default=os.environ.get("SERVICE_SLO_EXPECTED_REVISION") or None)
    parser.add_argument("--failure-threshold", type=_positive_integer, default=2)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            os.environ.get("SERVICE_SLO_STATE_FILE", "~/.local/state/service-slo-watchdog/state.json")
        ).expanduser(),
    )
    parser.add_argument("--timeout", type=_positive_float, default=5.0)
    parser.add_argument("--request-slo", type=_positive_float, default=5.0)
    parser.add_argument("--dependency-slo", type=_positive_float, default=2.5)
    parser.add_argument("--report", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.base_url:
        parser.error("--base-url or SERVICE_SLO_BASE_URL is required")
    metric_prefix = arguments.metric_prefix or arguments.service_name.replace("-", "_").replace(".", "_")
    try:
        dependencies = parse_dependencies(arguments.dependencies)
        contract = ProbeContract(
            service_name=arguments.service_name,
            metric_prefix=metric_prefix,
            dependencies=dependencies,
            request_slo_seconds=arguments.request_slo,
            dependency_slo_seconds=arguments.dependency_slo,
        )
    except ValueError as error:
        parser.error(str(error))

    result = probe(
        arguments.base_url,
        contract=contract,
        expected_revision=arguments.expected_revision,
        timeout=arguments.timeout,
    )
    if arguments.report:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1 if result["errors"] else 0

    message = update_alert_state(
        arguments.state_file,
        result,
        service_name=contract.service_name,
        failure_threshold=arguments.failure_threshold,
    )
    if message:
        print(message)
    return 0
