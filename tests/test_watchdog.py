import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from service_slo_watchdog.watchdog import ProbeContract, evaluate_probe, parse_metrics, update_alert_state


class WatchdogContractTest(unittest.TestCase):
    def setUp(self):
        self.contract = ProbeContract(
            service_name="orders-api",
            metric_prefix="orders_api",
            dependencies=("database", "queue"),
            request_slo_seconds=1.0,
            dependency_slo_seconds=0.5,
        )

    def healthy_metrics(self) -> str:
        return """orders_api_release_info{revision="abc123"} 1
orders_api_dependency_up{dependency="database"} 1e0
orders_api_dependency_up{dependency="queue"} 1
orders_api_dependency_check_duration_seconds{dependency="database"} 1.0e-2
orders_api_dependency_check_duration_seconds{dependency="queue"} 0.02
"""

    def test_public_package_exports_probe_api(self):
        import service_slo_watchdog as package

        self.assertIs(package.ProbeContract, ProbeContract)
        self.assertTrue(callable(package.probe))

    def test_accepts_a_generic_bounded_contract(self):
        result = evaluate_probe(
            contract=self.contract,
            version={"revision": "abc123", "service": "orders"},
            ready={"ok": True, "revision": "abc123", "status": "ready"},
            health={"ok": True, "status": "alive"},
            metrics_text=self.healthy_metrics(),
            pass_status=200,
            request_seconds={"version": 0.1, "ready": 0.2, "health": 0.1, "metrics": 0.2, "pass": 0.3},
            expected_revision="abc123",
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["revision"], "abc123")
        self.assertEqual(result["dependencies"], {"database": 1.0, "queue": 1.0})

    def test_reports_bounded_failures_without_copying_response_values(self):
        result = evaluate_probe(
            contract=self.contract,
            version={"revision": "old", "internal": "do-not-copy"},
            ready={"ok": False, "revision": "old", "status": "not_ready", "detail": "secret"},
            health={"ok": True, "status": "alive"},
            metrics_text=chr(10).join(
                [
                    'orders_api_dependency_up{dependency="database"} 1',
                    'orders_api_dependency_up{dependency="queue"} 0',
                    'orders_api_dependency_check_duration_seconds{dependency="database"} 0.1',
                    'orders_api_dependency_check_duration_seconds{dependency="queue"} 0.9',
                    "",
                ]
            ),
            pass_status=503,
            request_seconds={"version": 0.1, "ready": 1.5, "health": 0.1, "metrics": 0.2, "pass": 0.3},
            expected_revision="new",
        )
        rendered = json.dumps(result)
        for error in (
            "revision_mismatch",
            "ready_not_ok",
            "pass_http_status:503",
            "metrics_revision_missing",
            "dependency_down:queue",
            "dependency_slow:queue",
            "request_slow:ready",
        ):
            self.assertIn(error, result["errors"])
        self.assertNotIn("secret", rendered)
        self.assertNotIn("do-not-copy", rendered)

    def test_rejects_invalid_contract_identifiers(self):
        with self.assertRaises(ValueError):
            ProbeContract(service_name="bad" + chr(10) + "name", metric_prefix="orders_api", dependencies=("database",))
        with self.assertRaises(ValueError):
            ProbeContract(service_name="orders", metric_prefix="bad-prefix", dependencies=("database",))
        with self.assertRaises(ValueError):
            ProbeContract(service_name="orders", metric_prefix="orders", dependencies=("database", "database"))
        with self.assertRaises(ValueError):
            ProbeContract(service_name="orders", metric_prefix="orders", dependencies=())

    def test_parses_only_the_declared_metric_namespace_and_dependencies(self):
        metrics = parse_metrics(
            self.healthy_metrics()
            + chr(10).join(
                [
                    'other_release_info{revision="wrong"} 1',
                    'orders_api_dependency_up{dependency="cache"} 1',
                    "",
                ]
            ),
            self.contract,
        )
        self.assertEqual(metrics["revision"], "abc123")
        self.assertEqual(metrics["dependencies"], {"database": 1.0, "queue": 1.0})

    def test_duplicate_metric_series_fail_closed_instead_of_last_write_wins(self):
        metrics = chr(10).join(
            [
                'orders_api_release_info{revision="abc123"} 1',
                'orders_api_release_info{revision="abc123"} 1',
                'orders_api_dependency_up{dependency="database"} 0',
                'orders_api_dependency_up{dependency="database"} 1',
                'orders_api_dependency_up{dependency="queue"} 1',
                'orders_api_dependency_check_duration_seconds{dependency="database"} 0.01',
                'orders_api_dependency_check_duration_seconds{dependency="database"} 0.02',
                'orders_api_dependency_check_duration_seconds{dependency="queue"} 0.02',
                "",
            ]
        )
        result = evaluate_probe(
            contract=self.contract,
            version={"revision": "abc123"},
            ready={"ok": True, "revision": "abc123", "status": "ready"},
            health={"ok": True, "status": "alive"},
            metrics_text=metrics,
            pass_status=200,
            request_seconds={"version": 0.1, "ready": 0.1, "health": 0.1, "metrics": 0.1, "pass": 0.1},
            expected_revision="abc123",
        )
        self.assertIn("metrics_duplicate:release", result["errors"])
        self.assertIn("metrics_duplicate:dependency_up:database", result["errors"])
        self.assertIn("metrics_duplicate:dependency_duration:database", result["errors"])

    def test_unknown_alphanumeric_error_values_are_never_rendered(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory, "state.json")
            unknown = "AWSSECRETACCESSKEYEXAMPLE123456"
            message = update_alert_state(
                state_path,
                {"errors": [unknown, "ready_not_ok", "request_failed:metrics"], "revision": "abc123"},
                service_name="orders-api",
                failure_threshold=1,
            )
            self.assertEqual(
                message,
                "orders-api SLO alert: revision=abc123; failures=invalid_error,ready_not_ok,request_failed:metrics",
            )
            self.assertNotIn(unknown, message)

    def test_alerts_once_and_recovers_once_with_private_atomic_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory, "private", "state.json")
            failure = {"errors": ["ready_not_ok", "bad" + chr(10) + "secret"], "revision": "abc123"}
            healthy = {"errors": [], "revision": "abc123"}

            self.assertEqual(
                update_alert_state(state_path, failure, service_name="orders-api", failure_threshold=2), ""
            )
            alert = update_alert_state(state_path, failure, service_name="orders-api", failure_threshold=2)
            self.assertEqual(alert, "orders-api SLO alert: revision=abc123; failures=ready_not_ok,invalid_error")
            self.assertEqual(
                update_alert_state(state_path, failure, service_name="orders-api", failure_threshold=2), ""
            )
            recovery = update_alert_state(state_path, healthy, service_name="orders-api", failure_threshold=2)
            self.assertEqual(recovery, "orders-api SLO recovered: revision=abc123")
            self.assertEqual(
                update_alert_state(state_path, healthy, service_name="orders-api", failure_threshold=2), ""
            )
            self.assertEqual(stat.S_IMODE(os.stat(state_path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(state_path.parent).st_mode), 0o700)

    def test_non_object_and_inconsistent_state_cannot_crash_or_suppress_alerts(self):
        states = [
            None,
            [],
            "text",
            42,
            {"phase": "failing", "consecutiveFailures": "corrupt"},
            {"phase": "failing", "consecutiveFailures": 0},
        ]
        for index, state in enumerate(states):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                state_path = Path(directory, f"state-{index}.json")
                state_path.write_text(json.dumps(state), encoding="utf-8")
                message = update_alert_state(
                    state_path,
                    {"errors": ["health_not_ok"], "revision": "abc123"},
                    service_name="orders-api",
                    failure_threshold=1,
                )
                self.assertEqual(message, "orders-api SLO alert: revision=abc123; failures=health_not_ok")

    def test_corrupt_state_recovers_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory, "state.json")
            state_path.write_text("not-json", encoding="utf-8")
            message = update_alert_state(
                state_path,
                {"errors": ["health_not_ok"], "revision": "abc123"},
                service_name="orders-api",
                failure_threshold=1,
            )
            self.assertIn("orders-api SLO alert", message)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["phase"], "failing")

    def test_deeply_nested_state_recovers_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory, "state.json")
            state_path.write_text("[" * 1_500 + "0" + "]" * 1_500, encoding="utf-8")
            message = update_alert_state(
                state_path,
                {"errors": ["health_not_ok"], "revision": "abc123"},
                service_name="orders-api",
                failure_threshold=1,
            )
            self.assertEqual(message, "orders-api SLO alert: revision=abc123; failures=health_not_ok")
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["phase"], "failing")

    def test_post_replace_directory_fsync_failure_does_not_lose_alert_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory, "state.json")
            real_fsync = os.fsync
            calls = 0

            def fail_directory_fsync(descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated directory fsync failure")
                return real_fsync(descriptor)

            with mock.patch("service_slo_watchdog.watchdog.os.fsync", side_effect=fail_directory_fsync):
                message = update_alert_state(
                    state_path,
                    {"errors": ["health_not_ok"], "revision": "abc123"},
                    service_name="orders-api",
                    failure_threshold=1,
                )
            self.assertEqual(message, "orders-api SLO alert: revision=abc123; failures=health_not_ok")
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["phase"], "failing")


if __name__ == "__main__":
    unittest.main()
