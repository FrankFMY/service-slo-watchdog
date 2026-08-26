from __future__ import annotations

import contextlib
import http.client
import json
import math
import os
import re
import signal
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENDPOINT_NAMES = ("version", "ready", "health", "metrics", "pass")
_SERVICE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_METRIC_PREFIX_PATTERN = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,127}")
_DEPENDENCY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_REVISION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,127}")
_ERROR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_EXACT_ERRORS = frozenset(
    {
        "version_missing",
        "revision_mismatch",
        "ready_not_ok",
        "ready_revision_mismatch",
        "health_not_ok",
        "metrics_revision_missing",
        "metrics_revision_mismatch",
        "invalid_probe_result",
        "invalid_error",
    }
)
_DYNAMIC_ERROR_PATTERN = re.compile(
    r"(?:"
    r"pass_http_status:(?:0|[1-5][0-9]{2})|"
    r"(?:version|ready|health|metrics)_http_status:[1-5][0-9]{2}|"
    r"dependency_(?:down|duration_missing|slow):[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}|"
    r"metrics_duplicate:(?:release|dependency_(?:up|duration):[A-Za-z0-9][A-Za-z0-9_.:-]{0,63})|"
    r"request_(?:missing|slow|failed):(?:version|ready|health|metrics|pass)"
    r")"
)


@dataclass(frozen=True, slots=True)
class ProbeContract:
    service_name: str
    metric_prefix: str
    dependencies: tuple[str, ...]
    request_slo_seconds: float = 5.0
    dependency_slo_seconds: float = 2.5

    def __post_init__(self) -> None:
        if _SERVICE_NAME_PATTERN.fullmatch(self.service_name) is None:
            raise ValueError("service_name must be a bounded portable identifier")
        if _METRIC_PREFIX_PATTERN.fullmatch(self.metric_prefix) is None:
            raise ValueError("metric_prefix must be a valid Prometheus identifier")
        if not self.dependencies:
            raise ValueError("at least one dependency is required")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("dependencies must be unique")
        if any(_DEPENDENCY_PATTERN.fullmatch(item) is None for item in self.dependencies):
            raise ValueError("dependency names must be bounded portable identifiers")
        for name, value in (
            ("request_slo_seconds", self.request_slo_seconds),
            ("dependency_slo_seconds", self.dependency_slo_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


def _finite_number(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_metrics(text: str, contract: ProbeContract) -> dict[str, Any]:
    dependencies: dict[str, float] = {}
    durations: dict[str, float] = {}
    duplicates: list[str] = []
    duplicate_keys: set[str] = set()
    dependency_samples: set[str] = set()
    duration_samples: set[str] = set()
    release_seen = False
    revision = ""
    prefix = re.escape(contract.metric_prefix)
    declared_dependencies = "|".join(re.escape(item) for item in contract.dependencies)
    release_pattern = re.compile(rf'{prefix}_release_info\{{revision="([^"\\\n]+)"\}} ({_NUMBER_PATTERN})')
    dependency_pattern = re.compile(
        rf'{prefix}_dependency_up\{{dependency="({declared_dependencies})"\}} ({_NUMBER_PATTERN})'
    )
    duration_pattern = re.compile(
        rf'{prefix}_dependency_check_duration_seconds\{{dependency="({declared_dependencies})"\}} ({_NUMBER_PATTERN})'
    )

    for line in text.splitlines():
        release = release_pattern.fullmatch(line)
        if release:
            if release_seen:
                if "release" not in duplicate_keys:
                    duplicate_keys.add("release")
                    duplicates.append("metrics_duplicate:release")
                continue
            release_seen = True
            value = _finite_number(release.group(2))
            if value == 1.0 and _REVISION_PATTERN.fullmatch(release.group(1)):
                revision = release.group(1)
            continue
        dependency = dependency_pattern.fullmatch(line)
        if dependency:
            name = dependency.group(1)
            duplicate_key = f"dependency_up:{name}"
            if name in dependency_samples:
                if duplicate_key not in duplicate_keys:
                    duplicate_keys.add(duplicate_key)
                    duplicates.append(f"metrics_duplicate:{duplicate_key}")
                continue
            dependency_samples.add(name)
            value = _finite_number(dependency.group(2))
            if value is not None:
                dependencies[name] = value
            continue
        duration = duration_pattern.fullmatch(line)
        if duration:
            name = duration.group(1)
            duplicate_key = f"dependency_duration:{name}"
            if name in duration_samples:
                if duplicate_key not in duplicate_keys:
                    duplicate_keys.add(duplicate_key)
                    duplicates.append(f"metrics_duplicate:{duplicate_key}")
                continue
            duration_samples.add(name)
            value = _finite_number(duration.group(2))
            if value is not None and value >= 0:
                durations[name] = value

    return {"dependencies": dependencies, "durations": durations, "duplicates": duplicates, "revision": revision}


def _bounded_revision(value: object) -> str:
    return value if isinstance(value, str) and _REVISION_PATTERN.fullmatch(value) else ""


def evaluate_probe(
    *,
    contract: ProbeContract,
    version: dict[str, Any],
    ready: dict[str, Any],
    health: dict[str, Any],
    metrics_text: str,
    pass_status: int,
    request_seconds: dict[str, float],
    expected_revision: str | None,
) -> dict[str, Any]:
    errors: list[str] = []
    metrics = parse_metrics(metrics_text, contract)
    errors.extend(metrics["duplicates"])
    revision = _bounded_revision(version.get("revision"))
    expected = _bounded_revision(expected_revision) if expected_revision is not None else ""
    if not revision:
        errors.append("version_missing")
    if expected_revision is not None and (not expected or revision != expected):
        errors.append("revision_mismatch")
    if ready.get("ok") is not True or ready.get("status") != "ready":
        errors.append("ready_not_ok")
    if ready.get("revision") != revision:
        errors.append("ready_revision_mismatch")
    if health.get("ok") is not True or health.get("status") != "alive":
        errors.append("health_not_ok")
    if pass_status != 200:
        errors.append(f"pass_http_status:{pass_status}")
    if not metrics["revision"]:
        errors.append("metrics_revision_missing")
    elif metrics["revision"] != revision:
        errors.append("metrics_revision_mismatch")

    for dependency in contract.dependencies:
        if metrics["dependencies"].get(dependency) != 1.0:
            errors.append(f"dependency_down:{dependency}")
        duration = metrics["durations"].get(dependency)
        if duration is None:
            errors.append(f"dependency_duration_missing:{dependency}")
        elif duration > contract.dependency_slo_seconds:
            errors.append(f"dependency_slow:{dependency}")

    for path in _ENDPOINT_NAMES:
        duration = request_seconds.get(path)
        if duration is None or not math.isfinite(duration) or duration < 0:
            errors.append(f"request_missing:{path}")
        elif duration > contract.request_slo_seconds:
            errors.append(f"request_slow:{path}")

    return {
        "dependencies": {key: metrics["dependencies"].get(key, 0.0) for key in contract.dependencies},
        "errors": errors,
        "revision": revision or "unknown",
    }


def _write_state(path: Path, state: dict[str, Any]) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_existed:
        os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        with contextlib.suppress(OSError):
            directory_descriptor = os.open(path.parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _bounded_errors(value: object) -> list[str]:
    if not isinstance(value, list):
        return ["invalid_probe_result"]
    errors: list[str] = []
    for item in value[:32]:
        if (
            isinstance(item, str)
            and _ERROR_PATTERN.fullmatch(item)
            and (item in _EXACT_ERRORS or _DYNAMIC_ERROR_PATTERN.fullmatch(item))
        ):
            errors.append(item)
        else:
            errors.append("invalid_error")
    return errors


def update_alert_state(
    path: Path,
    result: dict[str, Any],
    *,
    service_name: str,
    failure_threshold: int = 2,
) -> str:
    if _SERVICE_NAME_PATTERN.fullmatch(service_name) is None:
        raise ValueError("service_name must be a bounded portable identifier")
    if type(failure_threshold) is not int or failure_threshold < 1:
        raise ValueError("failure_threshold must be a positive integer")
    try:
        loaded_state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state = loaded_state if isinstance(loaded_state, dict) else {}
    except (OSError, RecursionError, ValueError):
        state = {}
    phase = state.get("phase") if state.get("phase") in {"healthy", "failing"} else "healthy"
    stored_failures = state.get("consecutiveFailures", 0)
    failures_valid = type(stored_failures) is int and 0 <= stored_failures <= 1_000_000
    failures = stored_failures if failures_valid else 0
    phase_is_consistent = (phase == "failing") == (failures >= failure_threshold)
    if not failures_valid or not phase_is_consistent:
        phase = "healthy"
        failures = 0
    errors = _bounded_errors(result.get("errors"))
    revision = _bounded_revision(result.get("revision")) or "unknown"
    message = ""

    if errors:
        failures += 1
        if failures >= failure_threshold and phase != "failing":
            phase = "failing"
            message = f"{service_name} SLO alert: revision={revision}; failures={','.join(errors)}"
    else:
        if phase == "failing":
            message = f"{service_name} SLO recovered: revision={revision}"
        phase = "healthy"
        failures = 0

    _write_state(path, {"consecutiveFailures": failures, "phase": phase, "revision": revision})
    return message


@contextlib.contextmanager
def _wall_clock_deadline(seconds: float):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("watchdog timeout must be finite and positive")
    if not hasattr(signal, "setitimer"):
        raise RuntimeError("watchdog requires POSIX wall-clock timers")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def handle_timeout(_signum, _frame):
        raise TimeoutError("watchdog response exceeded total deadline")

    signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            elapsed = time.monotonic() - started
            signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_delay - elapsed), previous_interval)


def _validated_base_url(base_url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("watchdog URL must use HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("watchdog URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("watchdog base URL must not contain a query or fragment")
    return parsed


def _request(base_url: str, path: str, *, timeout: float, max_body_bytes: int) -> tuple[int, bytes, float]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("watchdog timeout must be finite and positive")
    if type(max_body_bytes) is not int or max_body_bytes < 0:
        raise ValueError("max_body_bytes must be a non-negative integer")
    base = _validated_base_url(base_url)
    nonce = str(time.time_ns())
    normalized_base = urllib.parse.urlunsplit((base.scheme, base.netloc, base.path.rstrip("/") + "/", "", ""))
    url = urllib.parse.urljoin(normalized_base, path.lstrip("/"))
    parsed = urllib.parse.urlsplit(url)
    assert parsed.hostname is not None
    query = [("probe", nonce)]
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", urllib.parse.urlencode(query), ""))
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    started = time.monotonic()
    try:
        with _wall_clock_deadline(timeout):
            connection.request("GET", target, headers={"User-Agent": "service-slo-watchdog/0.1"})
            response = connection.getresponse()
            if max_body_bytes == 0:
                return response.status, b"", time.monotonic() - started
            body = bytearray()
            deadline = started + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("watchdog response exceeded total deadline")
                if connection.sock is not None:
                    connection.sock.settimeout(max(0.001, remaining))
                chunk = response.read1(min(8_192, max_body_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_body_bytes:
                    raise ValueError("response body exceeds limit")
            return response.status, bytes(body), time.monotonic() - started
    finally:
        connection.close()


def probe(
    base_url: str,
    *,
    contract: ProbeContract,
    expected_revision: str | None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("watchdog timeout must be finite and positive")
    payloads: dict[str, Any] = {"version": {}, "ready": {}, "health": {}}
    metrics_text = ""
    pass_status = 0
    durations: dict[str, float] = {}
    transport_errors: list[str] = []

    for name, path in (
        ("version", "/version"),
        ("ready", "/ready"),
        ("health", "/health"),
        ("metrics", "/metrics"),
        ("pass", "/pass"),
    ):
        try:
            max_body_bytes = 0 if name == "pass" else 65_536 if name == "metrics" else 16_384
            status, body, duration = _request(base_url, path, timeout=timeout, max_body_bytes=max_body_bytes)
            durations[name] = duration
            if name == "pass":
                pass_status = status
            elif name == "metrics":
                if status == 200:
                    metrics_text = body.decode("utf-8", errors="strict")
                else:
                    transport_errors.append(f"metrics_http_status:{status}")
            elif status == 200:
                decoded = json.loads(body)
                if not isinstance(decoded, dict):
                    raise ValueError("JSON probe endpoint must return an object")
                payloads[name] = decoded
            else:
                transport_errors.append(f"{name}_http_status:{status}")
        except (
            OSError,
            RecursionError,
            TimeoutError,
            UnicodeError,
            ValueError,
            http.client.HTTPException,
        ):
            durations[name] = timeout
            transport_errors.append(f"request_failed:{name}")

    result = evaluate_probe(
        contract=contract,
        version=payloads["version"],
        ready=payloads["ready"],
        health=payloads["health"],
        metrics_text=metrics_text,
        pass_status=pass_status,
        request_seconds=durations,
        expected_revision=expected_revision,
    )
    result["errors"] = transport_errors + result["errors"]
    return result
