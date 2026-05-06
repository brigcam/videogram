import unittest

from app.transcripts import Transcript, parse_json3, parse_timed_text


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

    def test_parses_srt_text(self) -> None:
        raw = """1
00:00:00,000 --> 00:00:01,000
Hello world

2
00:00:01,000 --> 00:00:02,000
Next line
"""

        self.assertEqual(parse_timed_text(raw), "Hello world\nNext line")

    def test_transcript_round_trip_dict(self) -> None:
        transcript = Transcript("hello", "en", "manual")

        self.assertEqual(Transcript.from_dict(transcript.to_dict()), transcript)


if __name__ == "__main__":
    unittest.main()
