import contextlib
import io
import unittest
from unittest.mock import patch

from usage_monitor.__main__ import main, parse_providers


class ProviderArgumentTests(unittest.TestCase):
    def test_valid_provider_arguments_are_ordered(self):
        self.assertEqual(parse_providers("claude"), ["claude"])
        self.assertEqual(parse_providers("codex"), ["codex"])
        self.assertEqual(parse_providers("codex,claude"), ["claude", "codex"])

    def test_main_passes_provider_override_to_app(self):
        with patch("usage_monitor.ui.run") as run:
            self.assertEqual(main(["--providers", "claude"]), 0)
        run.assert_called_once_with(providers=["claude"])

    def test_invalid_provider_argument_exits_with_code_two(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(["--providers", "both"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("usa claude, codex o claude,codex", error.getvalue())


if __name__ == "__main__":
    unittest.main()
