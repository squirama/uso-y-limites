import unittest

from usage_monitor.models import UsageError, UsageSnapshot, WindowUsage, parse_codex, reset_text


NOW = 1791463800


class ModelTests(unittest.TestCase):
    def test_real_codex_response_shape(self):
        result = parse_codex({"rateLimitsByLimitId": {"codex": {"planType": "team",
            "primary": {"usedPercent": 47, "windowDurationMins": 300, "resetsAt": NOW + 3600},
            "secondary": {"usedPercent": 14, "windowDurationMins": 10080, "resetsAt": NOW + 86400},
            "credits": {"hasCredits": False, "unlimited": False, "balance": None}}}}, now=NOW)
        self.assertEqual([window.label for window in result.windows], ["5 horas", "7 días"])
        self.assertEqual(result.windows[0].used_percent, 47)
        self.assertEqual(result.detail, "Plan informado: Team")

    def test_prefers_multibucket_without_losing_models(self):
        result = parse_codex({"rateLimits": {"primary": {"usedPercent": 99}},
            "rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 10}},
                                   "model": {"limitName": "Modelo", "primary": {"usedPercent": 20}}}})
        self.assertEqual([w.used_percent for w in result.windows], [10, 20])
        self.assertTrue(result.windows[1].label.startswith("Modelo"))

    def test_missing_is_not_zero(self):
        with self.assertRaises(UsageError):
            parse_codex({"rateLimits": {"primary": {"usedPercent": None}}})
        result = parse_codex({"rateLimits": {"primary": {"usedPercent": 0}}})
        self.assertEqual(result.windows[0].used_percent, 0)

    def test_credit_only_account(self):
        result = parse_codex({"rateLimits": {"credits": {"balance": "42.5"}}})
        self.assertEqual(result.windows, ())
        self.assertIn("42.5 créditos", result.detail)
        self.assertIn("Sin porcentajes", result.detail)

    def test_rejects_invalid_numbers(self):
        for value in [True, "47", -1, 101, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(UsageError):
                parse_codex({"rateLimits": {"primary": {"usedPercent": value}}})

    def test_stale_and_reset_handling(self):
        snapshot = UsageSnapshot((WindowUsage("5 horas", 66, NOW + 100),), NOW, "test")
        self.assertFalse(snapshot.stale(NOW + 99))
        self.assertTrue(snapshot.stale(NOW + 100))
        self.assertTrue(UsageSnapshot((), NOW, "test").stale(NOW + 1861))
        self.assertIn("falta nueva lectura", reset_text(NOW, now=NOW + 1))
        self.assertEqual(reset_text(None), "Reinicio no informado")


if __name__ == "__main__":
    unittest.main()
