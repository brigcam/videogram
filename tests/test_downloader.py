import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.parse

from app.downloader import DownloadedAudio, DownloadedPhoto, DownloadedPost, DownloadError, VideoDownloader
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

    def test_selects_largest_thumbnail_url(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        self.assertEqual(
            downloader._select_thumbnail_url(
                {
                    "thumbnail": "https://example.com/default.jpg",
                    "thumbnails": [
                        {"url": "https://example.com/small.jpg", "width": 120, "height": 90},
                        {"url": "https://example.com/large.webp", "width": 1280, "height": 720},
                    ],
                }
            ),
            "https://example.com/large.webp",
        )

    def test_extracts_tiktok_photo_metadata_from_rehydration_json(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        data = {
            "__DEFAULT_SCOPE__": {
                "webapp.reflow.video.detail": {
                    "itemInfo": {
                        "itemStruct": {
                            "desc": "1980s Japan.. #tokyo",
                            "author": {"nickname": "Cosmo Bloom", "uniqueId": "cosmo_bloom"},
                            "music": {
                                "title": "Fantasy by Meiko Nakahara",
                                "authorName": "DIN",
                                "playUrl": "https://v16.example.com/music",
                                "duration": 39,
                            },
                            "imagePost": {
                                "images": [
                                    {
                                        "imageURL": {
                                            "urlList": [
                                                "https:\\u002F\\u002Fp16.example.com\\u002Fone~tplv-photomode-image.jpeg?x=1"
                                            ]
                                        }
                                    },
                                    {
                                        "imageURL": {
                                            "urlList": [
                                                "https://p16.example.com/two~tplv-photomode-image.webp?x=2"
                                            ]
                                        }
                                    },
                                ]
                            },
                        }
                    }
                }
            }
        }
        page_html = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            f"{json.dumps(data)}"
            "</script>"
        )

        metadata = downloader._extract_tiktok_photo_metadata(page_html)

        self.assertEqual(metadata["title"], "1980s Japan.. #tokyo")
        self.assertEqual(metadata["description"], "1980s Japan.. #tokyo")
        self.assertEqual(
            metadata["music"],
            {
                "url": "https://v16.example.com/music",
                "title": "Fantasy by Meiko Nakahara",
                "author": "DIN",
                "duration": 39,
            },
        )
        self.assertEqual(
            metadata["image_urls"],
            [
                "https://p16.example.com/one~tplv-photomode-image.jpeg?x=1",
                "https://p16.example.com/two~tplv-photomode-image.webp?x=2",
            ],
        )

    def test_collects_tiktok_photo_urls_from_html_fallback(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        page_html = (
            'href="https://p16.example.com/one~tplv-photomode-image.jpeg?x=1" '
            'href="https://p16.example.com/one~tplv-photomode-image.jpeg?x=1" '
            'href="https://p16.example.com/card~tplv-photomode-video-share-card:630:630:20.jpeg?x=2" '
            'href="https://p16.example.com/avatar.jpeg?x=3"'
        )

        self.assertEqual(
            downloader._collect_tiktok_photo_urls_from_html(page_html),
            ["https://p16.example.com/one~tplv-photomode-image.jpeg?x=1"],
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
                        "audio_filename": "music.mp3",
                        "audio_title": "Song",
                        "audio_description": "Artist",
                        "telegram_audio_file_id": "audio-file-id",
                        "text": "Post text",
                    }
                ),
                encoding="utf-8",
            )
            (cache_dir / "music.mp3").write_bytes(b"audio")

            post = downloader._load_cached_post(cache_dir, url)

            self.assertIsNotNone(post)
            self.assertEqual(post.photos[0].telegram_file_id, "photo-file-id")
            self.assertEqual(post.audio.title, "Song")
            self.assertEqual(post.audio.telegram_file_id, "audio-file-id")
            self.assertEqual(post.text, "Post text")

    def test_refreshes_old_tiktok_photo_cache_without_music_once(self) -> None:
        url = "https://vm.tiktok.com/ZMabcdef/"
        test_case = self

        class FakeDownloader(VideoDownloader):
            refresh_count = 0

            def _download_tiktok_photo_post_sync(
                self,
                url: str,
                request_id: str,
                preserve_photo_file_ids: list[str] | None = None,
            ):
                self.refresh_count += 1
                test_case.assertEqual(preserve_photo_file_ids, ["photo-file-id"])
                return DownloadedPost(
                    title="Post",
                    source_url=url,
                    cache_dir=self.cache_dir_for_url(url),
                    photos=(DownloadedPhoto(self.cache_dir_for_url(url) / "photo-01.jpg", "photo-file-id"),),
                    audio=DownloadedAudio(self.cache_dir_for_url(url) / "music.mp3", "Song", url, self.cache_dir_for_url(url)),
                    cached=True,
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = FakeDownloader(
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

            post = downloader._download_post_sync(url, "test-request")

            self.assertIsNotNone(post.audio)
            self.assertEqual(downloader.refresh_count, 1)

    def test_video_format_profiles_get_progressively_smaller(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        profiles = downloader._video_format_profiles(100)

        self.assertEqual(profiles[0][0], "video_1080p")
        self.assertEqual(profiles[1][0], "video_1080p_unknown_size")
        self.assertEqual(profiles[-2][0], "video_240p_unknown_size")
        self.assertIn("height<=240", profiles[-2][1])
        self.assertNotIn("filesize", profiles[-2][1])
        self.assertEqual(profiles[-1][0], "video_best_unknown_size")

    def test_selects_domain_specific_cookie_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookies_dir = tempfile.TemporaryDirectory()
            self.addCleanup(cookies_dir.cleanup)
            cookies_path = pathlib.Path(cookies_dir.name)
            (cookies_path / "instagram.txt").write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            (cookies_path / "youtube.txt").write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=100,
                min_free_disk_percent=0,
                cookies_dir=cookies_dir.name,
            )

            sources = downloader._cookie_sources_for_url("https://www.instagram.com/reel/DTgaWBUDlyZ/")

            self.assertEqual(sources, [cookies_path / "instagram.txt"])

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

    def test_video_metadata_prefers_info_values(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        metadata = downloader._video_metadata({"width": 1920, "height": 1080, "duration": 42}, pathlib.Path("/nope"))

        self.assertEqual(metadata, (1920, 1080, 42))

    def test_loads_cached_video_dimensions(self) -> None:
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
                        "width": 1280,
                        "height": 720,
                        "duration": 12,
                    }
                ),
                encoding="utf-8",
            )

            video = downloader._load_cached_video(cache_dir, url)

            self.assertIsNotNone(video)
            self.assertEqual(video.width, 1280)
            self.assertEqual(video.height, 720)
            self.assertEqual(video.duration, 12)

    def test_install_cache_files_preserves_summary_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = VideoDownloader(
                temp_dir,
                max_download_bytes=100,
                max_telegram_upload_bytes=100,
                min_free_disk_percent=0,
            )
            cache_dir = downloader.cache_dir_for_url("https://example.com/video")
            cache_dir.mkdir()
            (cache_dir / "old.mp4").write_bytes(b"old")
            (cache_dir / "metadata.json").write_text("{}", encoding="utf-8")
            (cache_dir / "transcript.json").write_text('{"transcript": {}}', encoding="utf-8")
            (cache_dir / "summary.json").write_text('{"summary": "cached"}', encoding="utf-8")
            (cache_dir / "summary.parameters.json").write_text('{"model": "test"}', encoding="utf-8")
            (cache_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

            part_dir = downloader.download_dir / "new.part"
            part_dir.mkdir()
            new_video = part_dir / "new.mp4"
            new_video.write_bytes(b"new")

            installed = downloader._install_cache_files(part_dir, cache_dir, (new_video,))

            self.assertEqual(installed, (cache_dir / "new.mp4",))
            self.assertTrue((cache_dir / "new.mp4").exists())
            self.assertFalse((cache_dir / "old.mp4").exists())
            self.assertFalse((cache_dir / "metadata.json").exists())
            self.assertTrue((cache_dir / "transcript.json").exists())
            self.assertTrue((cache_dir / "summary.json").exists())
            self.assertTrue((cache_dir / "summary.parameters.json").exists())
            self.assertTrue((cache_dir / "thumbnail.jpg").exists())
            self.assertFalse(part_dir.exists())

    def test_youtube_timedtext_fallback_extracts_transcript(self) -> None:
        test_case = self

        class FakeDownloader(VideoDownloader):
            def _download_subtitle_text(self, url: str) -> str:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                if query.get("type") == ["list"]:
                    return (
                        '<transcript_list>'
                        '<track lang_code="en" kind="asr" name="" lang_default="true" />'
                        "</transcript_list>"
                    )
                test_case.assertEqual(query.get("lang"), ["en"])
                test_case.assertEqual(query.get("kind"), ["asr"])
                return '{"events":[{"segs":[{"utf8":"Hello "},{"utf8":"world"}]}]}'

        downloader = FakeDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        transcript = downloader._extract_youtube_timedtext_transcript(
            {"id": "abc123"},
            "https://www.youtube.com/watch?v=abc123",
            "test-request",
            ("en",),
        )

        self.assertIsNotNone(transcript)
        self.assertEqual(transcript.text, "Hello world")
        self.assertEqual(transcript.language, "en")
        self.assertEqual(transcript.source, "youtube_timedtext")

    def test_youtube_timedtext_fallback_can_request_translation(self) -> None:
        test_case = self

        class FakeDownloader(VideoDownloader):
            def _download_subtitle_text(self, url: str) -> str:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                if query.get("type") == ["list"]:
                    return (
                        '<transcript_list>'
                        '<track lang_code="en" kind="asr" name="" lang_default="true" />'
                        '<target lang_code="it" cantran="true" />'
                        "</transcript_list>"
                    )
                test_case.assertEqual(query.get("lang"), ["en"])
                test_case.assertEqual(query.get("tlang"), ["it"])
                return '{"events":[{"segs":[{"utf8":"Ciao mondo"}]}]}'

        downloader = FakeDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        transcript = downloader._extract_youtube_timedtext_transcript(
            {"id": "abc123"},
            "https://www.youtube.com/watch?v=abc123",
            "test-request",
            ("it",),
        )

        self.assertIsNotNone(transcript)
        self.assertEqual(transcript.text, "Ciao mondo")
        self.assertEqual(transcript.language, "it")
        self.assertEqual(transcript.source, "youtube_timedtext_translated")

    def test_youtube_subtitle_download_error_is_not_silenced(self) -> None:
        class FakeDownloader(VideoDownloader):
            def _select_subtitle(self, info: dict, preferred_langs: tuple[str, ...]):
                return "it", "automatic", {"url": "https://example.com/subtitles", "ext": "json3"}

            def _download_subtitle_text(self, url: str) -> str:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

            def _extract_youtube_timedtext_transcript(
                self,
                info: dict,
                url: str,
                request_id: str,
                preferred_langs: tuple[str, ...],
            ):
                return None

        class FakeYDL:
            def __init__(self, options: dict) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                pass

            def extract_info(self, url: str, download: bool = False) -> dict:
                return {"id": "abc123"}

        downloader = FakeDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )

        import app.downloader as downloader_module

        original_youtube_dl = downloader_module.YoutubeDL
        downloader_module.YoutubeDL = FakeYDL
        try:
            with self.assertRaises(urllib.error.HTTPError):
                downloader._extract_transcript_once(
                    "https://www.youtube.com/watch?v=abc123",
                    "test-request",
                    ("it",),
                    {},
                )
        finally:
            downloader_module.YoutubeDL = original_youtube_dl

    def test_subtitle_selection_prefers_manual_video_language_before_preferred_auto(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        info = {
            "language": "en",
            "subtitles": {"en": [{"ext": "vtt", "url": "https://example.com/en.vtt"}]},
            "automatic_captions": {"it": [{"ext": "vtt", "url": "https://example.com/it.vtt"}]},
        }

        selected = downloader._select_subtitle(info, ("it", "en"))

        self.assertEqual(selected[0], "en")
        self.assertEqual(selected[1], "manual")

    def test_subtitle_selection_prefers_auto_video_language_before_preferred_manual(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        info = {
            "language": "en",
            "subtitles": {"it": [{"ext": "vtt", "url": "https://example.com/it.vtt"}]},
            "automatic_captions": {"en": [{"ext": "vtt", "url": "https://example.com/en.vtt"}]},
        }

        selected = downloader._select_subtitle(info, ("it", "en"))

        self.assertEqual(selected[0], "en")
        self.assertEqual(selected[1], "automatic")

    def test_subtitle_selection_falls_back_to_preferred_manual_then_any_manual(self) -> None:
        downloader = VideoDownloader(
            "/tmp",
            max_download_bytes=100,
            max_telegram_upload_bytes=100,
            min_free_disk_percent=0,
        )
        preferred_info = {
            "subtitles": {"en": [{"ext": "vtt", "url": "https://example.com/en.vtt"}]},
            "automatic_captions": {"it": [{"ext": "vtt", "url": "https://example.com/it.vtt"}]},
        }
        any_info = {
            "subtitles": {"fr": [{"ext": "vtt", "url": "https://example.com/fr.vtt"}]},
            "automatic_captions": {"it": [{"ext": "vtt", "url": "https://example.com/it.vtt"}]},
        }

        preferred = downloader._select_subtitle(preferred_info, ("en",))
        any_caption = downloader._select_subtitle(any_info, ("de",))

        self.assertEqual(preferred[0], "en")
        self.assertEqual(preferred[1], "manual")
        self.assertEqual(any_caption[0], "fr")
        self.assertEqual(any_caption[1], "manual")


if __name__ == "__main__":
    unittest.main()
