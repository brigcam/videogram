import unittest

from app.transcripts import parse_json3, parse_timed_text


class TranscriptTests(unittest.TestCase):
    def test_parses_vtt_text(self) -> None:
        raw = """WEBVTT

00:00:00.000 --> 00:00:01.000
Hello <c>world</c>

00:00:01.000 --> 00:00:02.000
Hello world

00:00:02.000 --> 00:00:03.000
Next line
"""

        self.assertEqual(parse_timed_text(raw), "Hello world\nNext line")

    def test_parses_json3_text(self) -> None:
        raw = '{"events":[{"segs":[{"utf8":"Hello "},{"utf8":"world"}]},{"segs":[{"utf8":"Next\\\\nline"}]}]}'

        self.assertEqual(parse_json3(raw), "Hello world\nNext line")


if __name__ == "__main__":
    unittest.main()
