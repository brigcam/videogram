from pathlib import Path
import unittest

from app.downloader import DownloadedAudio, DownloadedPhoto, DownloadedPost
from app.main import (
    SiteLimiter,
    build_help_text,
    cookie_command_usage,
    description_summary_message,
    normalize_netscape_cookie_text,
    parse_cookies_refresh_command_text,
    parse_cookie_command_text,
)


class DummyApplication:
    def __init__(self, bot_data: dict) -> None:
        self.bot_data = bot_data


class DummyContext:
    def __init__(self, bot_data: dict) -> None:
        self.application = DummyApplication(bot_data)


class MainTests(unittest.TestCase):
    def test_site_limiter_classifies_supported_sites(self) -> None:
        limiter = SiteLimiter(1)

        self.assertEqual(limiter.site_for_url("https://www.instagram.com/reel/DTgaWBUDlyZ/"), "instagram")
        self.assertEqual(limiter.site_for_url("https://youtu.be/abc12345"), "youtube")
        self.assertEqual(limiter.site_for_url("https://vm.tiktok.com/ZNRps4Evs/"), "tiktok")

    def test_parse_cookie_command_accepts_bot_username_and_payload(self) -> None:
        site, payload = parse_cookie_command_text("/cookie@VideogramBot instagram # Netscape HTTP Cookie File")

        self.assertEqual(site, "instagram")
        self.assertEqual(payload, "# Netscape HTTP Cookie File")

    def test_parse_cookie_command_rejects_unknown_site(self) -> None:
        with self.assertRaisesRegex(ValueError, "Sito non supportato"):
            parse_cookie_command_text("/cookie example value")

    def test_cookie_command_usage_mentions_reply_mode(self) -> None:
        self.assertIn("reply", cookie_command_usage())

    def test_help_text_includes_authorized_admin_commands(self) -> None:
        context = DummyContext(
            {
                "usage_allowed_user_ids": frozenset({123}),
                "cookie_allowed_user_ids": frozenset({123}),
            }
        )

        help_text = build_help_text(context, 123)

        self.assertIn("/usage", help_text)
        self.assertIn("/cookie_refresh all", help_text)

    def test_help_text_hides_admin_commands_when_not_authorized(self) -> None:
        context = DummyContext(
            {
                "usage_allowed_user_ids": frozenset({123}),
                "cookie_allowed_user_ids": frozenset({123}),
            }
        )

        help_text = build_help_text(context, 999)

        self.assertIn("/start", help_text)
        self.assertNotIn("/usage", help_text)
        self.assertNotIn("/cookie_refresh", help_text)

    def test_parse_cookies_refresh_command(self) -> None:
        self.assertEqual(parse_cookies_refresh_command_text("/cookies_refresh instagram"), ("instagram",))
        self.assertEqual(parse_cookies_refresh_command_text("/cookie_refresh twitter"), ("x",))
        self.assertEqual(parse_cookies_refresh_command_text("/cookie_refresh all"), ("all",))

    def test_parse_cookies_refresh_command_rejects_unknown_site(self) -> None:
        with self.assertRaisesRegex(ValueError, "non supportato"):
            parse_cookies_refresh_command_text("/cookies_refresh example")

    def test_normalize_netscape_cookie_text_adds_header(self) -> None:
        cookie = normalize_netscape_cookie_text(
            ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc123"
        )

        self.assertTrue(cookie.startswith("# Netscape HTTP Cookie File\n"))
        self.assertTrue(cookie.endswith("\n"))

    def test_normalize_netscape_cookie_text_accepts_httponly_rows(self) -> None:
        cookie = normalize_netscape_cookie_text(
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc123"
        )

        self.assertIn("#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc123", cookie)

    def test_normalize_netscape_cookie_text_rejects_invalid_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "formato Netscape"):
            normalize_netscape_cookie_text("not enough fields")

    def test_description_summary_for_audio_mentions_missing_transcript(self) -> None:
        post = DownloadedPost(
            "Audio",
            "https://example.com/audio",
            Path("/tmp/audio-cache"),
            audio=DownloadedAudio(Path("/tmp/audio.m4a"), "Audio", "https://example.com/audio", Path("/tmp/audio-cache")),
        )

        message = description_summary_message(post, "- **Punto**: testo")

        self.assertIn("Non ho trovato una trascrizione disponibile", message)
        self.assertIn("<b>Punto</b>", message)

    def test_description_summary_for_photo_has_no_transcript_note(self) -> None:
        post = DownloadedPost(
            "Foto",
            "https://example.com/photo",
            Path("/tmp/photo-cache"),
            photos=(DownloadedPhoto(Path("/tmp/photo.jpg")),),
        )

        message = description_summary_message(post, "- **Punto**: testo")

        self.assertNotIn("trascrizione", message)
        self.assertIn("<b>Punto</b>", message)


if __name__ == "__main__":
    unittest.main()
