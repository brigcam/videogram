import unittest

from app.captions import VIDEO_CAPTION_LIMIT, build_video_caption


class VideoCaptionTests(unittest.TestCase):
    def test_builds_title_and_description_quote(self) -> None:
        caption = build_video_caption(
            "https://example.com/video?a=1&b=2",
            "Titolo completo",
            "Prima riga\nSeconda riga",
        )

        self.assertIn(">Titolo completo</a>", caption)
        self.assertIn("<blockquote expandable>Prima riga\nSeconda riga</blockquote>", caption)
        self.assertIn("a=1&amp;b=2", caption)

    def test_trims_description_to_caption_limit(self) -> None:
        caption = build_video_caption("https://example.com/video", "Titolo", "x" * 5000)

        self.assertLessEqual(len(caption), VIDEO_CAPTION_LIMIT)
        self.assertIn("<blockquote expandable>", caption)
        self.assertIn("...", caption)


if __name__ == "__main__":
    unittest.main()
