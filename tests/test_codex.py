from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from usage_monitor.codex import CodexClient
from usage_monitor.models import UsageError


class CodexRpcTests(unittest.TestCase):
    def fake(self, code, timeout=2):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = Path(folder.name) / "server.py"
        script.write_text(code, encoding="utf-8")
        client = CodexClient([sys.executable, "-u", str(script)], timeout=timeout)
        self.addCleanup(client.close)
        return client

    def test_handshake_then_read_only_request(self):
        client = self.fake('''import json, sys
first = json.loads(sys.stdin.readline())
assert first['method'] == 'initialize'
print(json.dumps({'id': first['id'], 'result': {}}), flush=True)
assert json.loads(sys.stdin.readline())['method'] == 'initialized'
request = json.loads(sys.stdin.readline())
assert request['method'] == 'account/rateLimits/read'
print(json.dumps({'id': request['id'], 'result': {'rateLimits': {'primary': {'usedPercent': 12}}}}), flush=True)
sys.stdin.read()
''')
        result = client.fetch()
        self.assertEqual(result.windows[0].used_percent, 12)
        self.assertIsNone(client.process)

    def test_timeout_stops_process(self):
        client = self.fake("import time; time.sleep(30)", timeout=0.2)
        start = time.monotonic()
        with self.assertRaisesRegex(UsageError, "tiempo"):
            client.fetch()
        self.assertLess(time.monotonic() - start, 4)
        self.assertIsNone(client.process)

    def test_malformed_stdout_and_early_exit(self):
        for code in ["print('not json')", "pass"]:
            client = self.fake(code)
            with self.assertRaises(UsageError):
                client.fetch()
            self.assertIsNone(client.process)

    def test_error_does_not_expose_raw_provider_message(self):
        client = self.fake('''import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'id': request['id'], 'error': {'message': '401 unauthorized PRIVATE_DIAGNOSTIC'}}), flush=True)
sys.stdin.read()
''')
        with self.assertRaises(UsageError) as result:
            client.fetch()
        self.assertIn("sesión válida", str(result.exception))
        self.assertNotIn("PRIVATE_DIAGNOSTIC", str(result.exception))

    def test_close_cancels_inflight_read(self):
        client = self.fake("import time; time.sleep(30)")
        errors = []
        def run():
            try:
                client.fetch()
            except UsageError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 2
        while client.process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        client.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
