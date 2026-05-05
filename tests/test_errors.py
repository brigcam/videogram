import unittest

from app.errors import classify_download_error, classify_upload_error


class ErrorMessageTests(unittest.TestCase):
    def test_download_antibot_message(self) -> None:
        message = classify_download_error(RuntimeError("Sign in to confirm you're not a bot"))

        self.assertIn("anti-bot", message.title)
        self.assertIn("cookies", message.detail)

    def test_download_too_large_message(self) -> None:
        message = classify_download_error(RuntimeError("The downloaded video is larger than the configured limit."))

        self.assertIn("limite", message.title)
        self.assertIn("MAX_DOWNLOAD_MB", message.detail)

    def test_download_read_only_cookies_message(self) -> None:
        message = classify_download_error(RuntimeError("[Errno 30] Read-only file system: '/cookies/youtube.txt'"))

        self.assertIn("cookies", message.title)
        self.assertIn("mount", message.detail)

    def test_upload_too_large_message(self) -> None:
        message = classify_upload_error(RuntimeError("Request Entity Too Large"))

        self.assertIn("dimensione", message.title)
        self.assertIn("upload", message.detail)

    def test_download_reddit_auth_message(self) -> None:
        message = classify_download_error(RuntimeError("[Reddit] Account authentication is required"))

        self.assertIn("Reddit", message.title)
        self.assertIn("reddit.txt", message.detail)

    def test_message_includes_request_id(self) -> None:
        formatted = classify_download_error(RuntimeError("unknown")).format("abc123")

        self.assertIn("ID errore: abc123", formatted)


if __name__ == "__main__":
    unittest.main()
