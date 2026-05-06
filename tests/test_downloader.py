import json
import tempfile
import unittest

from app.downloader import DownloadError, VideoDownloader


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


if __name__ == "__main__":
    unittest.main()
