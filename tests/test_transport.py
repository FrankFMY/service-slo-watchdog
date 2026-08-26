import time
import unittest
from unittest import mock

from service_slo_watchdog.watchdog import ProbeContract, _request, probe


class TransportTest(unittest.TestCase):
    def test_does_not_read_the_pass_page_body(self):
        class Response:
            status = 200

            def read(self, _size):
                raise AssertionError("pass response body must not be read")

            def read1(self, _size):
                raise AssertionError("pass response body must not be read")

        class Socket:
            def settimeout(self, _timeout):
                pass

        class Connection:
            sock = Socket()

            def request(self, *_args, **_kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with mock.patch("service_slo_watchdog.watchdog.http.client.HTTPSConnection", return_value=Connection()):
            status, body, _duration = _request("https://example.com", "/pass", timeout=5, max_body_bytes=0)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_rejects_an_oversized_response_body(self):
        class Response:
            status = 200

            def __init__(self):
                self.calls = 0

            def read(self, _size):
                raise AssertionError("bounded reads must use read1")

            def read1(self, _size):
                self.calls += 1
                return b"12345" if self.calls == 1 else b""

        class Socket:
            def __init__(self):
                self.timeouts = []

            def settimeout(self, timeout):
                self.timeouts.append(timeout)

        class Connection:
            def __init__(self):
                self.sock = Socket()
                self.response = Response()

            def request(self, *_args, **_kwargs):
                pass

            def getresponse(self):
                return self.response

            def close(self):
                pass

        connection = Connection()
        with mock.patch("service_slo_watchdog.watchdog.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(ValueError, "response body exceeds limit"):
                _request("https://example.com", "/metrics", timeout=5, max_body_bytes=4)
        self.assertTrue(connection.sock.timeouts)

    def test_rechecks_the_total_deadline_after_each_socket_read(self):
        clock = [0.0]

        class Response:
            status = 200

            def read1(self, _size):
                clock[0] += 3.0
                return b"x"

        class Socket:
            def settimeout(self, _timeout):
                pass

        class Connection:
            sock = Socket()

            def request(self, *_args, **_kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with (
            mock.patch("service_slo_watchdog.watchdog.http.client.HTTPSConnection", return_value=Connection()),
            mock.patch("service_slo_watchdog.watchdog.time.monotonic", side_effect=lambda: clock[0]),
        ):
            with self.assertRaisesRegex(TimeoutError, "total deadline"):
                _request("https://example.com", "/metrics", timeout=5, max_body_bytes=64)

    def test_interrupts_a_blocked_chunk_read_at_the_wall_clock_deadline(self):
        class Response:
            status = 200

            def read1(self, _size):
                time.sleep(0.5)
                return b"x"

        class Socket:
            def settimeout(self, _timeout):
                pass

        class Connection:
            sock = Socket()

            def request(self, *_args, **_kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        started = time.monotonic()
        with mock.patch("service_slo_watchdog.watchdog.http.client.HTTPSConnection", return_value=Connection()):
            with self.assertRaisesRegex(TimeoutError, "total deadline"):
                _request("https://example.com", "/metrics", timeout=0.05, max_body_bytes=64)
        self.assertLess(time.monotonic() - started, 0.3)

    def test_rejects_non_http_and_credential_bearing_urls(self):
        for url in ("file:///tmp/service", "https://user:secret@example.com", "https://example.com/#fragment"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    _request(url, "/health", timeout=1, max_body_bytes=10)

    def test_deeply_nested_json_becomes_a_bounded_request_failure(self):
        deep_json = ("[" * 1_100 + "0" + "]" * 1_100).encode()

        def request(_base_url, path, **_kwargs):
            if path == "/version":
                return 200, deep_json, 0.01
            raise OSError("private detail")

        contract = ProbeContract(service_name="orders-api", metric_prefix="orders_api", dependencies=("database",))
        with mock.patch("service_slo_watchdog.watchdog._request", side_effect=request):
            result = probe("https://example.com", contract=contract, expected_revision="abc123", timeout=0.1)
        self.assertIn("request_failed:version", result["errors"])
        self.assertNotIn("private detail", str(result))

    def test_non_object_json_becomes_a_bounded_request_failure(self):
        def request(_base_url, path, **_kwargs):
            if path == "/version":
                return 200, b"[]", 0.01
            raise OSError("private detail")

        contract = ProbeContract(service_name="orders-api", metric_prefix="orders_api", dependencies=("database",))
        with mock.patch("service_slo_watchdog.watchdog._request", side_effect=request):
            result = probe("https://example.com", contract=contract, expected_revision="abc123", timeout=0.1)
        self.assertIn("request_failed:version", result["errors"])

    def test_probe_returns_bounded_transport_errors(self):
        contract = ProbeContract(service_name="orders-api", metric_prefix="orders_api", dependencies=("database",))
        with mock.patch("service_slo_watchdog.watchdog._request", side_effect=OSError("private network detail")):
            result = probe("https://example.com", contract=contract, expected_revision="abc123", timeout=0.1)
        rendered = str(result)
        self.assertIn("request_failed:version", result["errors"])
        self.assertIn("request_failed:pass", result["errors"])
        self.assertNotIn("private network detail", rendered)


if __name__ == "__main__":
    unittest.main()
