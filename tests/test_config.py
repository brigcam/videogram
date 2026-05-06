import unittest

from app.config import Settings, parse_allowed_chat_ids, parse_allowed_user_ids


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


if __name__ == "__main__":
    unittest.main()
