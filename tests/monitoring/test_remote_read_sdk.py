"""Offline wire tests, run in isolated monitoring-tools Python."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("remote_pb2"), "remote read SDK required")
class RemoteReadTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "remote_control", ROOT / "deploy/monitoring/prometheus/control.py"
        )
        assert spec and spec.loader
        self.control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.control)

    def test_wire_batch_encodes_labels_and_decodes_real_samples(self):
        import remote_pb2
        import snappy

        response = remote_pb2.ReadResponse()
        for number in (1.0, 2.0):
            result = response.results.add()
            result.timeseries.add().samples.add(timestamp=999000, value=number)
        session = Mock()
        session.post.return_value = SimpleNamespace(
            status_code=200, content=snappy.compress(response.SerializeToString())
        )
        transport = self.control.CloudTransport.__new__(self.control.CloudTransport)
        transport.config = self.control.load_config()
        transport.token = "private-token"
        transport.session = session
        selectors = [{"__name__": "one", "instance": "vm"}, {"__name__": "two"}]
        samples = transport.read_samples(selectors, now=1000)
        self.assertEqual(samples, [[[(999.0, 1.0)]], [[(999.0, 2.0)]]])
        args, kwargs = session.post.call_args
        self.assertTrue(args[0].endswith("/api/v1/read"))
        self.assertEqual(kwargs["headers"]["Content-Encoding"], "snappy")
        request = remote_pb2.ReadRequest.FromString(snappy.decompress(kwargs["data"]))
        self.assertEqual(len(request.queries), 2)
        self.assertEqual(request.queries[0].start_timestamp_ms, 820000)
        self.assertEqual(request.queries[0].end_timestamp_ms, 1000000)
        self.assertEqual({m.name: m.value for m in request.queries[0].matchers}, selectors[0])
        self.assertTrue(all(m.type == 0 for m in request.queries[0].matchers))

    def test_http_failure_does_not_reveal_response_or_token(self):
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=503, content=b"private-body")
        transport = self.control.CloudTransport.__new__(self.control.CloudTransport)
        transport.config = self.control.load_config()
        transport.token = "private-token"
        transport.session = session
        with patch.object(self.control.time, "sleep"):
            with self.assertRaises(self.control.ControlError) as error:
                transport.read_samples([{"__name__": "up"}], now=1000)
        self.assertEqual(session.post.call_count, 3)
        self.assertNotIn("private", str(error.exception))

    def test_timeout_retries_are_bounded_and_sanitized(self):
        import requests

        transport = self.control.CloudTransport.__new__(self.control.CloudTransport)
        transport.config = self.control.load_config()
        transport.token = "private-token"
        transport.session = Mock()
        transport.session.post.side_effect = requests.exceptions.Timeout("private-detail")
        with patch.object(self.control.time, "sleep") as sleep:
            with self.assertRaises(self.control.ControlError) as error:
                transport.read_samples([{"__name__": "up"}], now=1000)
        self.assertEqual(transport.session.post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn("private", str(error.exception))

    def test_authentication_error_fails_without_retry(self):
        session = Mock()
        session.post.return_value = SimpleNamespace(status_code=403, content=b"private")
        transport = self.control.CloudTransport.__new__(self.control.CloudTransport)
        transport.config = self.control.load_config()
        transport.token = "private-token"
        transport.session = session
        with self.assertRaises(self.control.ControlError):
            transport.read_samples([{"__name__": "up"}], now=1000)
        self.assertEqual(session.post.call_count, 1)
