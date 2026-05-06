import unittest

from app.links import (
    extract_supported_links,
    normalize_facebook_url,
    normalize_instagram_url,
    normalize_reddit_url,
    normalize_tiktok_url,
    normalize_threads_url,
    normalize_x_url,
    normalize_youtube_url,
)


class YoutubeLinkTests(unittest.TestCase):
    def test_normalizes_watch_urls(self) -> None:
        self.assertEqual(
            normalize_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_normalizes_short_urls(self) -> None:
        self.assertEqual(
            normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_extracts_unique_supported_links(self) -> None:
        text = (
            "Guarda https://youtu.be/dQw4w9WgXcQ e poi "
            "https://www.youtube.com/shorts/abcDEF12345!"
        )

        self.assertEqual(
            extract_supported_links(text),
            [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=abcDEF12345",
            ],
        )

    def test_ignores_unsupported_urls(self) -> None:
        self.assertEqual(extract_supported_links("https://example.com/video"), [])

    def test_normalizes_reddit_urls(self) -> None:
        self.assertEqual(
            normalize_reddit_url(
                "https://old.reddit.com/r/oddlyterrifying/comments/1t4tsgz/he_was_being_watched_the_entire_time/"
            ),
            "https://www.reddit.com/r/oddlyterrifying/comments/1t4tsgz/he_was_being_watched_the_entire_time/",
        )

    def test_normalizes_reddit_short_urls(self) -> None:
        self.assertEqual(
            normalize_reddit_url("https://redd.it/1t4tsgz"),
            "https://www.reddit.com/comments/1t4tsgz/",
        )

    def test_extracts_youtube_and_reddit_links(self) -> None:
        text = (
            "https://youtu.be/dQw4w9WgXcQ "
            "https://old.reddit.com/r/oddlyterrifying/comments/1t4tsgz/he_was_being_watched_the_entire_time/"
        )

        self.assertEqual(
            extract_supported_links(text),
            [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://www.reddit.com/r/oddlyterrifying/comments/1t4tsgz/he_was_being_watched_the_entire_time/",
            ],
        )

    def test_normalizes_instagram_reels(self) -> None:
        self.assertEqual(
            normalize_instagram_url("https://www.instagram.com/reel/C7abcDEF123/?igsh=tracking"),
            "https://www.instagram.com/reel/C7abcDEF123/",
        )

    def test_normalizes_instagram_posts(self) -> None:
        self.assertEqual(
            normalize_instagram_url("https://m.instagram.com/p/C7abcDEF123/"),
            "https://www.instagram.com/p/C7abcDEF123/",
        )

    def test_normalizes_facebook_watch_urls(self) -> None:
        self.assertEqual(
            normalize_facebook_url("https://www.facebook.com/watch/?v=123456789012345&ref=share"),
            "https://www.facebook.com/watch/?v=123456789012345",
        )

    def test_normalizes_facebook_reels(self) -> None:
        self.assertEqual(
            normalize_facebook_url("https://m.facebook.com/reel/123456789012345/"),
            "https://www.facebook.com/reel/123456789012345/",
        )

    def test_normalizes_threads_posts(self) -> None:
        self.assertEqual(
            normalize_threads_url("https://www.threads.net/@openai/post/C7abcDEF123?xmt=AQGz"),
            "https://www.threads.net/@openai/post/C7abcDEF123/",
        )

    def test_normalizes_threads_short_posts(self) -> None:
        self.assertEqual(
            normalize_threads_url("https://threads.com/t/C7abcDEF123"),
            "https://www.threads.net/t/C7abcDEF123/",
        )

    def test_normalizes_x_statuses(self) -> None:
        self.assertEqual(
            normalize_x_url("https://twitter.com/openai/status/1234567890123456789?s=20"),
            "https://x.com/openai/status/1234567890123456789",
        )

    def test_normalizes_tiktok_video_urls(self) -> None:
        self.assertEqual(
            normalize_tiktok_url("https://www.tiktok.com/@openai/video/1234567890123456789?is_from_webapp=1"),
            "https://www.tiktok.com/@openai/video/1234567890123456789",
        )

    def test_normalizes_tiktok_short_urls(self) -> None:
        self.assertEqual(
            normalize_tiktok_url("https://vm.tiktok.com/ZMabcdef/"),
            "https://vm.tiktok.com/ZMabcdef/",
        )

    def test_extracts_all_supported_social_links(self) -> None:
        text = (
            "https://www.instagram.com/reel/C7abcDEF123/ "
            "https://www.facebook.com/watch/?v=123456789012345 "
            "https://www.threads.net/@openai/post/C7abcDEF123 "
            "https://x.com/openai/status/1234567890123456789 "
            "https://www.tiktok.com/@openai/video/1234567890123456789"
        )

        self.assertEqual(
            extract_supported_links(text),
            [
                "https://www.instagram.com/reel/C7abcDEF123/",
                "https://www.facebook.com/watch/?v=123456789012345",
                "https://www.threads.net/@openai/post/C7abcDEF123/",
                "https://x.com/openai/status/1234567890123456789",
                "https://www.tiktok.com/@openai/video/1234567890123456789",
            ],
        )


if __name__ == "__main__":
    unittest.main()
