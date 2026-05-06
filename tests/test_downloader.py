import json
import tempfile
import unittest
import urllib.error

from app.downloader import DownloadError, VideoDownloader
from app.transcripts import TRANSCRIPT_CACHE_VERSION, Transcript


class DownloaderTests(unittest.TestCase):
    def test_cached_video_over_telegram_limit_is_rejected_before_upload(self) -> None:
        url = "https://example.com/video"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=10,
                min_free_disk_percent=0,
            )
            cache_dir = downloader.cache_dir_for_url(url)
            cache_dir.mkdir()
            video_path = cache_dir / "video.mp4"
            video_path.write_bytes(b"x" * 11)
            (cache_dir / "metadata.json").write_text(
                json.dumps({"source_url": url, "title": "Video", "filename": video_path.name}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DownloadError, "Telegram upload limit"):
                downloader._download_sync(url, "test-request")

    def test_transcript_rate_limit_is_retryable(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        error = urllib.error.HTTPError("https://example.com", 429, "Too Many Requests", {}, None)

        self.assertTrue(downloader._is_retryable_transcript_error(error))

    def test_saves_and_loads_telegram_file_id(self) -> None:
        url = "https://example.com/video"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=100,
                min_free_disk_percent=0,
            )
            cache_dir = downloader.cache_dir_for_url(url)
            cache_dir.mkdir()
            video_path = cache_dir / "video.mp4"
            video_path.write_bytes(b"x")
            (cache_dir / "metadata.json").write_text(
                json.dumps({"source_url": url, "title": "Video", "filename": video_path.name}),
                encoding="utf-8",
            )

            downloaded = downloader._load_cached_video(cache_dir, url)
            self.assertIsNotNone(downloaded)
            downloader.save_telegram_file_id(downloaded, "telegram-file-id")

            reloaded = downloader._load_cached_video(cache_dir, url)
            self.assertEqual(reloaded.telegram_file_id, "telegram-file-id")

    def test_saves_and_loads_transcript_document_file_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=100,
                min_free_disk_percent=0,
            )
            cache_dir = downloader.cache_dir_for_url("https://example.com/video")
            cache_dir.mkdir()
            preferred_langs = ("it", "en")
            (cache_dir / "transcript.json").write_text(
                json.dumps(
                    {
                        "cache_version": TRANSCRIPT_CACHE_VERSION,
                        "preferred_langs": list(preferred_langs),
                        "transcript": Transcript("ciao", "it", "manual").to_dict(),
                    }
                ),
                encoding="utf-8",
            )

            downloader.save_transcript_file_id(cache_dir, preferred_langs, "document-file-id")

            self.assertEqual(downloader.cached_transcript_file_id(cache_dir, preferred_langs), "document-file-id")

            downloader.forget_transcript_file_id(cache_dir, preferred_langs)

            self.assertEqual(downloader.cached_transcript_file_id(cache_dir, preferred_langs), "")


if __name__ == "__main__":
    unittest.main()
