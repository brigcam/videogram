import unittest

from app.links import extract_supported_links, normalize_youtube_url


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


if __name__ == "__main__":
    unittest.main()
