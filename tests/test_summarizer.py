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
        cache_key = summarizer._cache_key(transcript.text)

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            summarizer._write_cached_summary(cache_dir, cache_key, "Cached summary")

            self.assertEqual(summarizer._load_cached_summary(cache_dir, cache_key), "Cached summary")


if __name__ == "__main__":
    unittest.main()
