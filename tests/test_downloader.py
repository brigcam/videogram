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
                json.dumps(
                    {
                        "source_url": url,
                        "title": "Video",
                        "description": "Desc",
                        "filename": video_path.name,
                        "max_download_bytes": 100,
                    }
                ),
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
                json.dumps(
                    {
                        "source_url": url,
                        "title": "Video",
                        "description": "Desc",
                        "filename": video_path.name,
                        "max_download_bytes": 50,
                    }
                ),
                encoding="utf-8",
            )

            downloaded = downloader._load_cached_video(cache_dir, url)
            self.assertIsNotNone(downloaded)
            self.assertEqual(downloaded.description, "Desc")
            self.assertEqual(downloaded.cached_max_download_bytes, 50)
            self.assertTrue(downloader._cached_video_needs_quality_refresh(downloaded))
            downloader.save_telegram_file_id(downloaded, "telegram-file-id")
            downloader._mark_video_cache_checked_for_current_limit(cache_dir)

            reloaded = downloader._load_cached_video(cache_dir, url)
            self.assertEqual(reloaded.telegram_file_id, "telegram-file-id")
            self.assertEqual(reloaded.cached_max_download_bytes, 100)
            self.assertFalse(downloader._cached_video_needs_quality_refresh(reloaded))

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

    def test_collects_image_urls_from_entries(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        info = {
            "entries": [
                {"url": "https://example.com/one.jpg", "ext": "jpg"},
                {"url": "https://example.com/two.mp4", "ext": "mp4"},
                {"thumbnail": "https://example.com/three.webp"},
            ]
        }

        self.assertEqual(
            downloader._collect_image_urls(info),
            ["https://example.com/one.jpg", "https://example.com/three.webp"],
        )

    def test_loads_cached_photo_post(self) -> None:
        url = "https://example.com/post"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=100,
                min_free_disk_percent=0,
            )
            cache_dir = downloader.cache_dir_for_url(url)
            cache_dir.mkdir()
            photo_path = cache_dir / "photo-01.jpg"
            photo_path.write_bytes(b"x")
            (cache_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "source_url": url,
                        "title": "Post",
                        "description": "Desc",
                        "content_type": "photo",
                        "photo_filenames": [photo_path.name],
                        "telegram_photo_file_ids": ["photo-file-id"],
                        "text": "Post text",
                    }
                ),
                encoding="utf-8",
            )

            post = downloader._load_cached_post(cache_dir, url)

            self.assertIsNotNone(post)
            self.assertEqual(post.photos[0].telegram_file_id, "photo-file-id")
            self.assertEqual(post.text, "Post text")

    def test_video_format_profiles_get_progressively_smaller(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        profiles = downloader._video_format_profiles(100)

        self.assertEqual(profiles[0][0], "video_1080p")
        self.assertEqual(profiles[-1][0], "video_240p")
        self.assertIn("height<=240", profiles[-1][1])

    def test_loads_cached_audio_post(self) -> None:
        url = "https://example.com/video"
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=100,
                min_free_disk_percent=0,
            )
            cache_dir = downloader.cache_dir_for_url(f"audio:{url}")
            cache_dir.mkdir()
            audio_path = cache_dir / "audio.m4a"
            audio_path.write_bytes(b"x")
            (cache_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "source_url": url,
                        "title": "Audio",
                        "description": "Desc",
                        "content_type": "audio",
                        "filename": audio_path.name,
                        "telegram_audio_file_id": "audio-file-id",
                    }
                ),
                encoding="utf-8",
            )

            audio = downloader._load_cached_audio(cache_dir, url)

            self.assertIsNotNone(audio)
            self.assertEqual(audio.telegram_file_id, "audio-file-id")


if __name__ == "__main__":
    unittest.main()
