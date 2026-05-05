import unittest

from app.config import parse_allowed_chat_ids


class ConfigTests(unittest.TestCase):
    def test_empty_allowed_chat_ids_disables_whitelist(self) -> None:
        self.assertEqual(parse_allowed_chat_ids(""), frozenset())

    def test_parses_allowed_chat_ids(self) -> None:
        self.assertEqual(parse_allowed_chat_ids("111111111, -1001234567890"), frozenset({111111111, -1001234567890}))

    def test_rejects_invalid_allowed_chat_ids(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ALLOWED_CHAT_IDS"):
            parse_allowed_chat_ids("111111111, nope")


if __name__ == "__main__":
    unittest.main()
