import unittest

from app.config import Settings, parse_allowed_chat_ids, parse_allowed_user_ids, parse_bool, parse_optional_int


class ConfigTests(unittest.TestCase):
    def test_empty_allowed_chat_ids_disables_whitelist(self) -> None:
        self.assertEqual(parse_allowed_chat_ids(""), frozenset())

    def test_parses_allowed_chat_ids(self) -> None:
        self.assertEqual(parse_allowed_chat_ids("111111111, -1001234567890"), frozenset({111111111, -1001234567890}))

    def test_rejects_invalid_allowed_chat_ids(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ALLOWED_CHAT_IDS"):
            parse_allowed_chat_ids("111111111, nope")

    def test_parses_allowed_user_ids(self) -> None:
        self.assertEqual(parse_allowed_user_ids("111111111, 42"), frozenset({111111111, 42}))

    def test_rejects_invalid_allowed_user_ids(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ALLOWED_USER_IDS"):
            parse_allowed_user_ids("111111111, nope")

    def test_upload_limit_is_separate_from_download_limit(self) -> None:
        settings = Settings(telegram_bot_token="token", max_download_mb=512, max_telegram_upload_mb=48)

        self.assertEqual(settings.max_download_bytes, 512 * 1024 * 1024)
        self.assertEqual(settings.max_telegram_upload_bytes, 48 * 1024 * 1024)

    def test_queue_and_failure_defaults(self) -> None:
        settings = Settings(telegram_bot_token="token")

        self.assertEqual(settings.max_concurrent_jobs, 2)
        self.assertEqual(settings.site_concurrent_jobs, 1)
        self.assertEqual(settings.failed_links_file, "/var/log/videogram/failed-links.jsonl")
        self.assertEqual(settings.usage_check_interval_minutes, 60)
        self.assertEqual(settings.usage_alert_step_percent, 10)
        self.assertEqual(settings.cookie_allowed_user_ids, frozenset())
        self.assertEqual(settings.cookie_alert_user_id, 0)
        self.assertEqual(settings.hetzner_monthly_traffic_tb, 20.0)
        self.assertEqual(settings.openai_monthly_budget_usd, 0.0)
        self.assertEqual(settings.browser_profile_dir, "/browser-profiles")
        self.assertEqual(settings.browser_chromium_executable, "/usr/bin/chromium")

    def test_parse_optional_int(self) -> None:
        self.assertEqual(parse_optional_int(""), 0)
        self.assertEqual(parse_optional_int(" 123 "), 123)

    def test_parse_bool(self) -> None:
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("1"))
        self.assertFalse(parse_bool(""))
        self.assertFalse(parse_bool("false"))


if __name__ == "__main__":
    unittest.main()
