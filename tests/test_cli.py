import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from service_slo_watchdog.cli import main, parse_dependencies


class CliTest(unittest.TestCase):
    def test_parses_a_unique_dependency_list(self):
        self.assertEqual(parse_dependencies("database, queue"), ("database", "queue"))
        with self.assertRaises(ValueError):
            parse_dependencies("")
        with self.assertRaises(ValueError):
            parse_dependencies("database,database")

    def test_requires_an_explicit_base_url(self):
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main([])
        self.assertEqual(raised.exception.code, 2)

    def test_report_mode_prints_bounded_json_and_returns_nonzero(self):
        result = {"dependencies": {"database": 0.0}, "errors": ["dependency_down:database"], "revision": "abc123"}
        output = io.StringIO()
        with mock.patch("service_slo_watchdog.cli.probe", return_value=result) as probe_mock, redirect_stdout(output):
            code = main(
                [
                    "--base-url",
                    "https://example.com",
                    "--service-name",
                    "orders-api",
                    "--metric-prefix",
                    "orders_api",
                    "--dependencies",
                    "database",
                    "--expected-revision",
                    "abc123",
                    "--report",
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue()), result)
        contract = probe_mock.call_args.kwargs["contract"]
        self.assertEqual(contract.service_name, "orders-api")
        self.assertEqual(contract.metric_prefix, "orders_api")
        self.assertEqual(contract.dependencies, ("database",))

    def test_quiet_mode_emits_only_a_state_transition_and_returns_zero(self):
        failure = {"dependencies": {"database": 0.0}, "errors": ["dependency_down:database"], "revision": "abc123"}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory, "state.json")
            arguments = [
                "--base-url",
                "https://example.com",
                "--service-name",
                "orders-api",
                "--metric-prefix",
                "orders_api",
                "--dependencies",
                "database",
                "--failure-threshold",
                "1",
                "--state-file",
                str(state),
            ]
            output = io.StringIO()
            with mock.patch("service_slo_watchdog.cli.probe", return_value=failure), redirect_stdout(output):
                code = main(arguments)
            self.assertEqual(code, 0)
            self.assertEqual(
                output.getvalue().strip(), "orders-api SLO alert: revision=abc123; failures=dependency_down:database"
            )

            output = io.StringIO()
            with mock.patch("service_slo_watchdog.cli.probe", return_value=failure), redirect_stdout(output):
                code = main(arguments)
            self.assertEqual(code, 0)
            self.assertEqual(output.getvalue(), "")

    def test_environment_values_are_supported_without_product_defaults(self):
        env = {
            "SERVICE_SLO_BASE_URL": "https://example.com",
            "SERVICE_SLO_SERVICE_NAME": "billing-api",
            "SERVICE_SLO_METRIC_PREFIX": "billing_api",
            "SERVICE_SLO_DEPENDENCIES": "database,queue",
        }
        healthy = {"dependencies": {"database": 1.0, "queue": 1.0}, "errors": [], "revision": "abc123"}
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("service_slo_watchdog.cli.probe", return_value=healthy) as probe_mock,
        ):
            code = main(["--state-file", str(Path(directory, "state.json"))])
        self.assertEqual(code, 0)
        contract = probe_mock.call_args.kwargs["contract"]
        self.assertEqual(contract.service_name, "billing-api")
        self.assertEqual(contract.dependencies, ("database", "queue"))


if __name__ == "__main__":
    unittest.main()
