import json
import tempfile
import unittest
from pathlib import Path

from app.failures import FailureRecorder


class FailureRecorderTests(unittest.TestCase):
    def test_records_failed_link_as_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "failed-links.jsonl"
            recorder = FailureRecorder(path, max_error_chars=20)

            recorder.record(
                request_id="abc123",
                url="https://www.tiktok.com/@user/photo/12345",
                stage="download",
                error=RuntimeError("Something went wrong with a very long message"),
                chat_type="supergroup",
            )

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["request_id"], "abc123")
            self.assertEqual(record["stage"], "download")
            self.assertEqual(record["platform"], "www.tiktok.com")
            self.assertEqual(record["chat_type"], "supergroup")
            self.assertEqual(record["error_type"], "RuntimeError")
            self.assertLessEqual(len(record["error"]), 23)


if __name__ == "__main__":
    unittest.main()
