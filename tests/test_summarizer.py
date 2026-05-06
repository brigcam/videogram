import tempfile
import unittest
from pathlib import Path

from app.summarizer import OpenAISummarizer
from app.transcripts import Transcript


class SummarizerCacheTests(unittest.TestCase):
    def test_summary_cache_key_depends_on_prompt(self) -> None:
        first = OpenAISummarizer("key", "gpt-5.2", "prompt one", 20000)
        second = OpenAISummarizer("key", "gpt-5.2", "prompt two", 20000)

        self.assertNotEqual(first._cache_key("same transcript"), second._cache_key("same transcript"))

    def test_loads_cached_summary(self) -> None:
        summarizer = OpenAISummarizer("key", "gpt-5.2", "prompt", 20000)
        transcript = Transcript("hello world", "en", "manual")
        parameters = summarizer._summary_parameters(transcript.text)

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            summarizer._write_cached_summary(cache_dir, parameters, "Cached summary")

            self.assertEqual(summarizer._load_cached_summary(cache_dir, parameters), "Cached summary")

    def test_summary_cache_depends_on_api_key_fingerprint(self) -> None:
        first = OpenAISummarizer("key-one", "gpt-5.2", "prompt", 20000)
        second = OpenAISummarizer("key-two", "gpt-5.2", "prompt", 20000)

        self.assertNotEqual(first._cache_key("same transcript"), second._cache_key("same transcript"))

    def test_summary_cache_depends_on_content_kind(self) -> None:
        summarizer = OpenAISummarizer("key", "gpt-5.2", "prompt", 20000)

        transcript_parameters = summarizer._summary_parameters("same text", "transcript")
        description_parameters = summarizer._summary_parameters("same text", "description")

        self.assertNotEqual(transcript_parameters["parameters_sha256"], description_parameters["parameters_sha256"])

    def test_writes_summary_parameters_file(self) -> None:
        summarizer = OpenAISummarizer("key", "gpt-5.2", "prompt", 20000)
        parameters = summarizer._summary_parameters("hello")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            summarizer._write_cached_summary(cache_dir, parameters, "Cached summary")

            self.assertTrue((cache_dir / "summary.parameters.json").exists())


if __name__ == "__main__":
    unittest.main()
