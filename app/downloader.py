import asyncio
import hashlib
import json
import logging
import shutil
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.transcripts import TRANSCRIPT_CACHE_VERSION, Transcript, parse_subtitle_text
from yt_dlp import YoutubeDL


logger = logging.getLogger(__name__)


SIDECAR_CACHE_FILENAMES = frozenset(("transcript.json", "summary.json", "summary.parameters.json"))


class DownloadError(RuntimeError):
    pass


class TranscriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedVideo:
    path: Path
    title: str
    source_url: str
    cache_dir: Path
    description: str = ""
    cached: bool = False
    delete_after_send: bool = False
    telegram_file_id: str = ""
    cached_max_download_bytes: int = 0


@dataclass(frozen=True)
class DownloadedPhoto:
    path: Path
    telegram_file_id: str = ""


@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    title: str
    source_url: str
    cache_dir: Path
    description: str = ""
    cached: bool = False
    delete_after_send: bool = False
    telegram_file_id: str = ""


@dataclass(frozen=True)
class DownloadedPost:
    title: str
    source_url: str
    cache_dir: Path
    description: str = ""
    video: DownloadedVideo | None = None
    audio: DownloadedAudio | None = None
    photos: tuple[DownloadedPhoto, ...] = ()
    text: str = ""
    cached: bool = False
    delete_after_send: bool = False


class VideoDownloader:
    def __init__(
        self,
        download_dir: str,
        max_download_bytes: int,
        max_telegram_upload_bytes: int,
        min_free_disk_percent: float,
        cookies_file: str = "",
        cookies_dir: str = "",
    ) -> None:
        self.download_dir = Path(download_dir)
        self.max_download_bytes = max_download_bytes
        self.max_telegram_upload_bytes = max_telegram_upload_bytes
        self.min_free_disk_percent = min_free_disk_percent
        self.cookies_file = Path(cookies_file) if cookies_file else None
        self.cookies_dir = Path(cookies_dir) if cookies_dir else None
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str, request_id: str) -> DownloadedVideo:
        return await asyncio.to_thread(self._download_sync, url, request_id)

    async def download_post(self, url: str, request_id: str) -> DownloadedPost:
        return await asyncio.to_thread(self._download_post_sync, url, request_id)

    async def extract_transcript(
        self,
        url: str,
        request_id: str,
        preferred_langs: tuple[str, ...],
    ) -> Transcript | None:
        return await asyncio.to_thread(self._extract_transcript_sync, url, request_id, preferred_langs)

    def cache_dir_for_url(self, url: str) -> Path:
        return self.download_dir / hashlib.sha256(url.encode("utf-8")).hexdigest()

    def remove(self, path: Path) -> None:
        if path.exists() and path.is_dir() and path.parent == self.download_dir:
            logger.info("request cleanup removing_cache_dir path=%s", path)
            shutil.rmtree(path, ignore_errors=True)
            return
        parent = path.parent
        if parent.exists() and parent.parent == self.download_dir:
            logger.info("request cleanup removing_cache_dir path=%s", parent)
            shutil.rmtree(parent, ignore_errors=True)

    def _download_sync(self, url: str, request_id: str) -> DownloadedVideo:
        cache_dir = self.cache_dir_for_url(url)
        cache_key = cache_dir.name
        cached = self._load_cached_video(cache_dir, url)
        if cached:
            size_bytes = cached.path.stat().st_size
            if size_bytes > self.max_telegram_upload_bytes:
                logger.warning(
                    "request_id=%s cache_hit_too_large key=%s path=%s size_bytes=%s "
                    "max_telegram_upload_bytes=%s",
                    request_id,
                    cache_key[:12],
                    cached.path,
                    size_bytes,
                    self.max_telegram_upload_bytes,
                )
                raise DownloadError("The cached video is larger than the Telegram upload limit.")
            if self._cached_video_needs_quality_refresh(cached):
                logger.info(
                    "request_id=%s cache_quality_refresh_start key=%s cached_max_download_bytes=%s "
                    "current_max_download_bytes=%s",
                    request_id,
                    cache_key[:12],
                    cached.cached_max_download_bytes,
                    self.max_download_bytes,
                )
                try:
                    return self._download_best_video(url, request_id, cache_dir, cache_key)
                except DownloadError as exc:
                    logger.warning(
                        "request_id=%s cache_quality_refresh_failed key=%s error=%s",
                        request_id,
                        cache_key[:12],
                        exc,
                    )
                    self._mark_video_cache_checked_for_current_limit(cache_dir)
            self._touch_cache(cache_dir)
            logger.info(
                "request_id=%s cache_hit key=%s path=%s size_bytes=%s",
                request_id,
                cache_key[:12],
                cached.path,
                cached.path.stat().st_size,
            )
            return cached

        logger.info("request_id=%s cache_miss key=%s url=%s", request_id, cache_key[:12], url)
        self._prune_cache()
        return self._download_best_video(url, request_id, cache_dir, cache_key)

    def _download_best_video(
        self,
        url: str,
        request_id: str,
        cache_dir: Path,
        cache_key: str,
    ) -> DownloadedVideo:
        format_max_bytes = min(self.max_download_bytes, self.max_telegram_upload_bytes)
        last_error: Exception | None = None
        for profile_name, format_selector in self._video_format_profiles(format_max_bytes):
            try:
                return self._download_video_attempt(url, request_id, cache_dir, profile_name, format_selector)
            except DownloadError as exc:
                last_error = exc
                if not self._should_try_smaller_format(exc):
                    raise
                logger.warning(
                    "request_id=%s download_try_smaller_format key=%s profile=%s error=%s",
                    request_id,
                    cache_key[:12],
                    profile_name,
                    exc,
                )
        if last_error:
            raise last_error
        raise DownloadError("No downloadable video format was found.")

    def _cached_video_needs_quality_refresh(self, cached: DownloadedVideo) -> bool:
        return cached.cached_max_download_bytes < self.max_download_bytes

    def _mark_video_cache_checked_for_current_limit(self, cache_dir: Path) -> None:
        self._update_metadata(cache_dir, {"max_download_bytes": self.max_download_bytes})

    def _download_video_attempt(
        self,
        url: str,
        request_id: str,
        cache_dir: Path,
        profile_name: str,
        format_selector: str,
    ) -> DownloadedVideo:
        started_at = time.perf_counter()
        cache_key = cache_dir.name
        temp_dir = self.download_dir / f"{uuid.uuid4().hex}.part"
        temp_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "format": format_selector,
            "outtmpl": str(temp_dir / "%(title).180B [%(id)s].%(ext)s"),
            "merge_output_format": "mp4",
            "max_filesize": self.max_download_bytes,
            "js_runtimes": {"node": {}},
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        runtime_cookies = self._prepare_cookiefile(temp_dir, request_id)
        if runtime_cookies:
            options["cookiefile"] = str(runtime_cookies)

        try:
            logger.info("request_id=%s download_start url=%s profile=%s", request_id, url, profile_name)
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.warning("request_id=%s download_failed url=%s error=%s", request_id, url, exc)
            raise DownloadError(str(exc)) from exc

        path = Path(filename)
        if not path.exists():
            mp4_files = sorted(temp_dir.glob("*.mp4"), key=lambda item: item.stat().st_mtime)
            if not mp4_files:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.warning("request_id=%s download_missing_output url=%s", request_id, url)
                raise DownloadError("Download completed, but no video file was produced.")
            path = mp4_files[-1]

        if path.stat().st_size > self.max_download_bytes:
            size_bytes = path.stat().st_size
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.warning(
                "request_id=%s download_too_large size_bytes=%s max_download_bytes=%s url=%s",
                request_id,
                size_bytes,
                self.max_download_bytes,
                url,
            )
            raise DownloadError(
                "The downloaded video is larger than the configured limit "
                f"(size_bytes={size_bytes} max_bytes={self.max_download_bytes})."
            )

        if path.stat().st_size > self.max_telegram_upload_bytes:
            size_bytes = path.stat().st_size
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.warning(
                "request_id=%s download_too_large_for_telegram size_bytes=%s "
                "max_telegram_upload_bytes=%s url=%s",
                request_id,
                size_bytes,
                self.max_telegram_upload_bytes,
                url,
            )
            raise DownloadError(
                "The downloaded video is larger than the Telegram upload limit "
                f"(size_bytes={size_bytes} max_bytes={self.max_telegram_upload_bytes})."
            )

        cached_path = self._install_cache_files(temp_dir, cache_dir, (path,))[0]
        title = info.get("title") or "Video"
        description = info.get("description") or ""
        self._write_metadata(cache_dir, url, title, description, cached_path.name)
        self._prune_cache(exclude=cache_dir)
        delete_after_send = self._free_disk_percent() < self.min_free_disk_percent
        logger.info(
            "request_id=%s download_complete key=%s path=%s size_bytes=%s elapsed_ms=%s "
            "free_disk_percent=%.2f delete_after_send=%s",
            request_id,
            cache_key[:12],
            cached_path,
            cached_path.stat().st_size,
            int((time.perf_counter() - started_at) * 1000),
            self._free_disk_percent(),
            delete_after_send,
        )

        return DownloadedVideo(
            path=cached_path,
            title=title,
            source_url=url,
            cache_dir=cache_dir,
            description=description,
            delete_after_send=delete_after_send,
        )

    def _download_audio_sync(self, url: str, request_id: str) -> DownloadedAudio:
        cache_dir = self.cache_dir_for_url(f"audio:{url}")
        cached = self._load_cached_audio(cache_dir, url)
        if cached:
            self._touch_cache(cache_dir)
            logger.info(
                "request_id=%s audio_cache_hit key=%s path=%s size_bytes=%s",
                request_id,
                cache_dir.name[:12],
                cached.path,
                cached.path.stat().st_size,
            )
            return cached

        temp_dir = self.download_dir / f"{uuid.uuid4().hex}.audio.part"
        temp_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "format": f"ba[ext=m4a][filesize<={self.max_download_bytes}]/ba[filesize<={self.max_download_bytes}]/bestaudio",
            "outtmpl": str(temp_dir / "%(title).180B [%(id)s].%(ext)s"),
            "max_filesize": self.max_download_bytes,
            "js_runtimes": {"node": {}},
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        runtime_cookies = self._prepare_cookiefile(temp_dir, request_id)
        if runtime_cookies:
            options["cookiefile"] = str(runtime_cookies)

        try:
            logger.info("request_id=%s audio_download_start url=%s", request_id, url)
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.warning("request_id=%s audio_download_failed url=%s error=%s", request_id, url, exc)
            raise DownloadError(str(exc)) from exc

        path = Path(filename)
        if not path.exists():
            audio_files = sorted(
                [item for item in temp_dir.iterdir() if item.is_file() and item.name != "cookies.txt"],
                key=lambda item: item.stat().st_mtime,
            )
            if not audio_files:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise DownloadError("Audio download completed, but no audio file was produced.")
            path = audio_files[-1]

        size_bytes = path.stat().st_size
        if size_bytes > self.max_download_bytes:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloadError(
                "The downloaded audio is larger than the configured limit "
                f"(size_bytes={size_bytes} max_bytes={self.max_download_bytes})."
            )

        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_dir.rename(cache_dir)
        cached_path = cache_dir / path.name
        title = info.get("title") or "Audio"
        description = info.get("description") or ""
        self._write_audio_metadata(cache_dir, url, title, description, cached_path.name)
        delete_after_send = self._free_disk_percent() < self.min_free_disk_percent
        logger.info(
            "request_id=%s audio_download_complete key=%s path=%s size_bytes=%s delete_after_send=%s",
            request_id,
            cache_dir.name[:12],
            cached_path,
            cached_path.stat().st_size,
            delete_after_send,
        )
        return DownloadedAudio(
            path=cached_path,
            title=title,
            source_url=url,
            cache_dir=cache_dir,
            description=description,
            delete_after_send=delete_after_send,
        )

    def _video_format_profiles(self, max_bytes: int) -> tuple[tuple[str, str], ...]:
        return tuple(
            (
                f"video_{height}p",
                (
                    f"bv*[ext=mp4][height<={height}][filesize<={max_bytes}]+ba[ext=m4a]/"
                    f"bv*[ext=mp4][height<={height}][filesize_approx<={max_bytes}]+ba[ext=m4a]/"
                    f"b[ext=mp4][height<={height}][filesize<={max_bytes}]/"
                    f"b[ext=mp4][height<={height}][filesize_approx<={max_bytes}]/"
                    f"bv*[height<={height}][filesize<={max_bytes}]+ba/"
                    f"b[height<={height}][filesize<={max_bytes}]"
                ),
            )
            for height in (1080, 720, 480, 360, 240)
        )

    def _should_try_smaller_format(self, error: Exception) -> bool:
        message = str(error).lower()
        return (
            "larger than the configured limit" in message
            or "larger than the telegram upload limit" in message
            or "file is larger than max-filesize" in message
            or "requested format is not available" in message
        )

    def _download_post_sync(self, url: str, request_id: str) -> DownloadedPost:
        cached_post = self._load_cached_post(self.cache_dir_for_url(url), url)
        if cached_post:
            self._touch_cache(cached_post.cache_dir)
            logger.info(
                "request_id=%s post_cache_hit key=%s type=%s photo_count=%s",
                request_id,
                cached_post.cache_dir.name[:12],
                "photo" if cached_post.photos else "text",
                len(cached_post.photos),
            )
            return cached_post

        try:
            video = self._download_sync(url, request_id)
            return DownloadedPost(
                title=video.title,
                source_url=video.source_url,
                cache_dir=video.cache_dir,
                description=video.description,
                video=video,
                cached=video.cached,
                delete_after_send=video.delete_after_send,
            )
        except DownloadError as video_error:
            if self._should_try_audio_fallback(video_error):
                try:
                    audio = self._download_audio_sync(url, request_id)
                    return DownloadedPost(
                        title=audio.title,
                        source_url=audio.source_url,
                        cache_dir=audio.cache_dir,
                        description=audio.description,
                        audio=audio,
                        cached=audio.cached,
                        delete_after_send=audio.delete_after_send,
                    )
                except DownloadError as audio_error:
                    logger.warning("request_id=%s audio_fallback_failed url=%s error=%s", request_id, url, audio_error)
            try:
                return self._download_non_video_post_sync(url, request_id)
            except DownloadError:
                raise video_error

    def _should_try_audio_fallback(self, error: Exception) -> bool:
        message = str(error).lower()
        return (
            "larger than the configured limit" in message
            or "larger than the telegram upload limit" in message
            or "file is larger than max-filesize" in message
            or "requested format is not available" in message
        )

    def _download_non_video_post_sync(self, url: str, request_id: str) -> DownloadedPost:
        cache_dir = self.cache_dir_for_url(url)
        cache_key = cache_dir.name
        logger.info("request_id=%s post_extract_start key=%s url=%s", request_id, cache_key[:12], url)
        self._prune_cache()
        temp_dir = self.download_dir / f"{uuid.uuid4().hex}.post.part"
        temp_dir.mkdir(parents=True, exist_ok=True)

        options = {
            "skip_download": True,
            "noplaylist": False,
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
        }
        runtime_cookies = self._prepare_cookiefile(temp_dir, request_id)
        if runtime_cookies:
            options["cookiefile"] = str(runtime_cookies)

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.warning("request_id=%s post_extract_failed url=%s error=%s", request_id, url, exc)
            raise DownloadError(str(exc)) from exc

        title = info.get("title") or "Post"
        description = info.get("description") or ""
        image_urls = self._collect_image_urls(info)
        photo_paths: list[Path] = []
        total_bytes = 0
        for index, image_url in enumerate(image_urls[:10], start=1):
            try:
                photo_path = self._download_image(image_url, temp_dir, index)
            except Exception as exc:
                logger.warning(
                    "request_id=%s post_image_download_failed index=%s url=%s error=%s",
                    request_id,
                    index,
                    image_url,
                    exc,
                )
                continue
            total_bytes += photo_path.stat().st_size
            if total_bytes > self.max_download_bytes:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise DownloadError("The downloaded post media is larger than the configured limit.")
            photo_paths.append(photo_path)

        text = description or title
        if not photo_paths and not text.strip():
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloadError("No downloadable media or text was found for this post.")

        installed_photo_paths = self._install_cache_files(temp_dir, cache_dir, tuple(photo_paths))
        photos = tuple(DownloadedPhoto(path) for path in installed_photo_paths)
        self._write_post_metadata(cache_dir, url, title, description, text, photos)
        delete_after_send = self._free_disk_percent() < self.min_free_disk_percent
        logger.info(
            "request_id=%s post_extract_complete key=%s photo_count=%s text_chars=%s "
            "free_disk_percent=%.2f delete_after_send=%s",
            request_id,
            cache_key[:12],
            len(photos),
            len(text),
            self._free_disk_percent(),
            delete_after_send,
        )
        return DownloadedPost(
            title=title,
            source_url=url,
            cache_dir=cache_dir,
            description=description,
            photos=photos,
            text=text,
            delete_after_send=delete_after_send,
        )

    def _collect_image_urls(self, info: dict) -> list[str]:
        urls: list[str] = []

        def add_url(candidate: str | None) -> None:
            if not candidate or candidate in urls:
                return
            path = urlparse(candidate).path.lower()
            if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                urls.append(candidate)

        def walk(item) -> None:
            if not isinstance(item, dict):
                return
            ext = (item.get("ext") or "").lower()
            if ext in {"jpg", "jpeg", "png", "webp"}:
                add_url(item.get("url"))
            add_url(item.get("thumbnail"))
            thumbnails = item.get("thumbnails") or []
            for thumbnail in sorted(thumbnails, key=lambda entry: entry.get("width") or 0, reverse=True):
                add_url(thumbnail.get("url"))
            for entry in item.get("entries") or []:
                walk(entry)

        walk(info)
        return urls

    def _install_cache_files(self, temp_dir: Path, cache_dir: Path, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for child in list(cache_dir.iterdir()):
            if child.name in SIDECAR_CACHE_FILENAMES:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                continue
            child.unlink(missing_ok=True)

        installed_paths: list[Path] = []
        for path in paths:
            target = cache_dir / path.name
            if target.exists():
                target.unlink()
            path.rename(target)
            installed_paths.append(target)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return tuple(installed_paths)

    def _download_image(self, url: str, temp_dir: Path, index: int) -> Path:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        path = temp_dir / f"photo-{index:02d}{suffix}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            path.write_bytes(response.read())
        return path

    def save_telegram_file_id(self, downloaded: DownloadedVideo, file_id: str) -> None:
        if not file_id:
            return
        self._update_metadata(downloaded.cache_dir, {"telegram_file_id": file_id})

    def forget_telegram_file_id(self, downloaded: DownloadedVideo) -> None:
        self._update_metadata(downloaded.cache_dir, {"telegram_file_id": ""})

    def save_post_photo_file_ids(self, post: DownloadedPost, file_ids: list[str]) -> None:
        if not file_ids:
            return
        self._update_metadata(post.cache_dir, {"telegram_photo_file_ids": file_ids})

    def forget_post_photo_file_ids(self, post: DownloadedPost) -> None:
        self._update_metadata(post.cache_dir, {"telegram_photo_file_ids": []})

    def save_audio_file_id(self, audio: DownloadedAudio, file_id: str) -> None:
        if not file_id:
            return
        self._update_metadata(audio.cache_dir, {"telegram_audio_file_id": file_id})

    def forget_audio_file_id(self, audio: DownloadedAudio) -> None:
        self._update_metadata(audio.cache_dir, {"telegram_audio_file_id": ""})

    def cached_transcript_file_id(self, cache_dir: Path, preferred_langs: tuple[str, ...]) -> str:
        metadata = self._load_cached_transcript_metadata(cache_dir, preferred_langs)
        if not metadata:
            return ""
        return metadata.get("telegram_document_file_id") or ""

    def save_transcript_file_id(self, cache_dir: Path, preferred_langs: tuple[str, ...], file_id: str) -> None:
        if not file_id:
            return
        self._update_cached_transcript_metadata(cache_dir, preferred_langs, {"telegram_document_file_id": file_id})

    def forget_transcript_file_id(self, cache_dir: Path, preferred_langs: tuple[str, ...]) -> None:
        self._update_cached_transcript_metadata(cache_dir, preferred_langs, {"telegram_document_file_id": ""})

    def _extract_transcript_sync(
        self,
        url: str,
        request_id: str,
        preferred_langs: tuple[str, ...],
    ) -> Transcript | None:
        cache_dir = self.cache_dir_for_url(url)
        cached_transcript = self._load_cached_transcript(cache_dir, preferred_langs)
        if cached_transcript:
            logger.info(
                "request_id=%s transcript_cache_hit url=%s language=%s source=%s chars=%s",
                request_id,
                url,
                cached_transcript.language,
                cached_transcript.source,
                len(cached_transcript.text),
            )
            self._touch_cache(cache_dir)
            return cached_transcript

        temp_dir = self.download_dir / f"{uuid.uuid4().hex}.transcript.part"
        temp_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "skip_download": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
        }
        runtime_cookies = self._prepare_cookiefile(temp_dir, request_id)
        if runtime_cookies:
            options["cookiefile"] = str(runtime_cookies)

        try:
            return self._extract_transcript_with_retries(cache_dir, url, request_id, preferred_langs, options)
        except Exception as exc:
            logger.warning("request_id=%s transcript_extract_failed url=%s error=%s", request_id, url, exc)
            raise TranscriptError(str(exc)) from exc
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _extract_transcript_with_retries(
        self,
        cache_dir: Path,
        url: str,
        request_id: str,
        preferred_langs: tuple[str, ...],
        options: dict,
    ) -> Transcript | None:
        max_attempts = 3
        retry_delays = (3, 8)
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(
                    "request_id=%s transcript_extract_start url=%s attempt=%s max_attempts=%s",
                    request_id,
                    url,
                    attempt,
                    max_attempts,
                )
                transcript = self._extract_transcript_once(url, request_id, preferred_langs, options)
                if transcript:
                    self._write_cached_transcript(cache_dir, transcript, preferred_langs)
                return transcript
            except Exception as exc:
                if not self._is_retryable_transcript_error(exc):
                    raise
                if attempt >= max_attempts:
                    raise
                delay_seconds = retry_delays[attempt - 1]
                logger.warning(
                    "request_id=%s transcript_extract_retry url=%s attempt=%s "
                    "next_attempt=%s delay_seconds=%s error=%s",
                    request_id,
                    url,
                    attempt,
                    attempt + 1,
                    delay_seconds,
                    exc,
                )
                time.sleep(delay_seconds)
        return None

    def _extract_transcript_once(
        self,
        url: str,
        request_id: str,
        preferred_langs: tuple[str, ...],
        options: dict,
    ) -> Transcript | None:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        subtitle = self._select_subtitle(info, preferred_langs)
        if not subtitle:
            logger.info("request_id=%s transcript_not_found url=%s", request_id, url)
            return None

        language, source, subtitle_format = subtitle
        raw_text = self._download_subtitle_text(subtitle_format["url"])
        transcript_text = parse_subtitle_text(raw_text, subtitle_format.get("ext") or "")
        if not transcript_text:
            logger.info(
                "request_id=%s transcript_empty url=%s language=%s source=%s",
                request_id,
                url,
                language,
                source,
            )
            return None

        logger.info(
            "request_id=%s transcript_extract_complete url=%s language=%s source=%s chars=%s",
            request_id,
            url,
            language,
            source,
            len(transcript_text),
        )
        return Transcript(text=transcript_text, language=language, source=source)

    def _is_retryable_transcript_error(self, error: Exception) -> bool:
        if isinstance(error, urllib.error.HTTPError) and error.code in {429, 500, 502, 503, 504}:
            return True
        message = str(error).lower()
        return (
            "http error 429" in message
            or "too many requests" in message
            or "temporarily unavailable" in message
            or "timed out" in message
            or "timeout" in message
        )

    def _select_subtitle(
        self,
        info: dict,
        preferred_langs: tuple[str, ...],
    ) -> tuple[str, str, dict] | None:
        manual = info.get("subtitles") or {}
        automatic = info.get("automatic_captions") or {}
        for source, subtitles in (("manual", manual), ("automatic", automatic)):
            for language in self._rank_subtitle_languages(subtitles, preferred_langs):
                subtitle_format = self._best_subtitle_format(subtitles.get(language) or [])
                if subtitle_format:
                    return language, source, subtitle_format
        return None

    def _rank_subtitle_languages(self, subtitles: dict, preferred_langs: tuple[str, ...]) -> list[str]:
        languages = list(subtitles.keys())
        ranked: list[str] = []
        for preferred in preferred_langs:
            preferred = preferred.lower()
            for language in languages:
                if language in ranked:
                    continue
                normalized = language.lower()
                if normalized == preferred or normalized.startswith(f"{preferred}-"):
                    ranked.append(language)
        ranked.extend(language for language in languages if language not in ranked)
        return ranked

    def _best_subtitle_format(self, formats: list[dict]) -> dict | None:
        preferred_exts = ("json3", "vtt", "srv3", "ttml")
        for ext in preferred_exts:
            for subtitle_format in formats:
                if subtitle_format.get("ext") == ext and subtitle_format.get("url"):
                    return subtitle_format
        for subtitle_format in formats:
            if subtitle_format.get("url"):
                return subtitle_format
        return None

    def _download_subtitle_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")

    def _load_cached_transcript(self, cache_dir: Path, preferred_langs: tuple[str, ...]) -> Transcript | None:
        metadata = self._load_cached_transcript_metadata(cache_dir, preferred_langs)
        if not metadata:
            return None
        transcript = Transcript.from_dict(metadata.get("transcript") or {})
        if not transcript:
            return None
        return transcript

    def _load_cached_transcript_metadata(self, cache_dir: Path, preferred_langs: tuple[str, ...]) -> dict | None:
        metadata_path = cache_dir / "transcript.json"
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if metadata.get("cache_version") != TRANSCRIPT_CACHE_VERSION:
            return None
        if tuple(metadata.get("preferred_langs") or ()) != preferred_langs:
            return None
        return metadata

    def _write_cached_transcript(
        self,
        cache_dir: Path,
        transcript: Transcript,
        preferred_langs: tuple[str, ...],
    ) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "cache_version": TRANSCRIPT_CACHE_VERSION,
            "created_at": time.time(),
            "preferred_langs": list(preferred_langs),
            "transcript": transcript.to_dict(),
        }
        (cache_dir / "transcript.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _update_cached_transcript_metadata(
        self,
        cache_dir: Path,
        preferred_langs: tuple[str, ...],
        updates: dict,
    ) -> None:
        metadata_path = cache_dir / "transcript.json"
        metadata = self._load_cached_transcript_metadata(cache_dir, preferred_langs)
        if not metadata:
            return
        metadata.update(updates)
        try:
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _prepare_cookiefile(self, temp_dir: Path, request_id: str) -> Path | None:
        cookie_sources: list[Path] = []
        if self.cookies_file:
            cookie_sources.append(self.cookies_file)
        if self.cookies_dir:
            if self.cookies_dir.exists():
                cookie_sources.extend(sorted(self.cookies_dir.glob("*.txt")))
            else:
                logger.warning("request_id=%s ytdlp_cookies_dir_missing path=%s", request_id, self.cookies_dir)

        existing_sources: list[Path] = []
        seen: set[Path] = set()
        for source in cookie_sources:
            resolved = source.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if source.exists() and source.is_file():
                existing_sources.append(source)
            else:
                logger.warning("request_id=%s ytdlp_cookies_missing path=%s", request_id, source)

        if not existing_sources:
            return None

        runtime_cookies = temp_dir / "cookies.txt"
        with runtime_cookies.open("w", encoding="utf-8") as output:
            output.write("# Netscape HTTP Cookie File\n")
            for source in existing_sources:
                last_line = "\n"
                with source.open("r", encoding="utf-8", errors="replace") as input_file:
                    for line in input_file:
                        last_line = line
                        if line.startswith("# Netscape HTTP Cookie File"):
                            continue
                        output.write(line)
                if not last_line.endswith("\n"):
                    output.write("\n")

        logger.info(
            "request_id=%s ytdlp_cookies_enabled source_count=%s runtime_path=%s",
            request_id,
            len(existing_sources),
            runtime_cookies,
        )
        return runtime_cookies

    def _load_cached_video(self, cache_dir: Path, url: str) -> DownloadedVideo | None:
        metadata_path = cache_dir / "metadata.json"
        if not metadata_path.exists():
            return None

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        filename = metadata.get("filename")
        if metadata.get("source_url") != url or not filename:
            return None

        video_path = cache_dir / filename
        if not video_path.exists() or not video_path.is_file():
            return None

        return DownloadedVideo(
            path=video_path,
            title=metadata.get("title") or "Video",
            source_url=url,
            cache_dir=cache_dir,
            description=metadata.get("description") or "",
            cached=True,
            telegram_file_id=metadata.get("telegram_file_id") or "",
            cached_max_download_bytes=int(metadata.get("max_download_bytes") or 0),
        )

    def _load_cached_post(self, cache_dir: Path, url: str) -> DownloadedPost | None:
        metadata_path = cache_dir / "metadata.json"
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if metadata.get("source_url") != url or metadata.get("content_type") not in {"audio", "photo", "text"}:
            return None

        if metadata.get("content_type") == "audio":
            audio = self._load_cached_audio(cache_dir, url)
            if not audio:
                return None
            return DownloadedPost(
                title=audio.title,
                source_url=url,
                cache_dir=cache_dir,
                description=audio.description,
                audio=audio,
                cached=True,
            )

        photo_file_ids = metadata.get("telegram_photo_file_ids") or []
        photos: list[DownloadedPhoto] = []
        for index, filename in enumerate(metadata.get("photo_filenames") or []):
            path = cache_dir / filename
            if path.exists() and path.is_file():
                file_id = photo_file_ids[index] if index < len(photo_file_ids) else ""
                photos.append(DownloadedPhoto(path=path, telegram_file_id=file_id))

        return DownloadedPost(
            title=metadata.get("title") or "Post",
            source_url=url,
            cache_dir=cache_dir,
            description=metadata.get("description") or "",
            photos=tuple(photos),
            text=metadata.get("text") or "",
            cached=True,
        )

    def _load_cached_audio(self, cache_dir: Path, url: str) -> DownloadedAudio | None:
        metadata_path = cache_dir / "metadata.json"
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        filename = metadata.get("filename")
        if metadata.get("source_url") != url or metadata.get("content_type") != "audio" or not filename:
            return None
        audio_path = cache_dir / filename
        if not audio_path.exists() or not audio_path.is_file():
            return None
        return DownloadedAudio(
            path=audio_path,
            title=metadata.get("title") or "Audio",
            source_url=url,
            cache_dir=cache_dir,
            description=metadata.get("description") or "",
            cached=True,
            telegram_file_id=metadata.get("telegram_audio_file_id") or "",
        )

    def _write_metadata(self, cache_dir: Path, url: str, title: str, description: str, filename: str) -> None:
        now = time.time()
        metadata = {
            "source_url": url,
            "title": title,
            "description": description,
            "filename": filename,
            "max_download_bytes": self.max_download_bytes,
            "created_at": now,
            "last_accessed_at": now,
        }
        (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _write_audio_metadata(self, cache_dir: Path, url: str, title: str, description: str, filename: str) -> None:
        now = time.time()
        metadata = {
            "source_url": url,
            "title": title,
            "description": description,
            "content_type": "audio",
            "filename": filename,
            "created_at": now,
            "last_accessed_at": now,
        }
        (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _write_post_metadata(
        self,
        cache_dir: Path,
        url: str,
        title: str,
        description: str,
        text: str,
        photos: tuple[DownloadedPhoto, ...],
    ) -> None:
        now = time.time()
        metadata = {
            "source_url": url,
            "title": title,
            "description": description,
            "content_type": "photo" if photos else "text",
            "photo_filenames": [photo.path.name for photo in photos],
            "text": text,
            "created_at": now,
            "last_accessed_at": now,
        }
        (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _touch_cache(self, cache_dir: Path) -> None:
        self._update_metadata(cache_dir, {"last_accessed_at": time.time()})

    def _update_metadata(self, cache_dir: Path, updates: dict) -> None:
        metadata_path = cache_dir / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(updates)
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass

    def _prune_cache(self, exclude: Path | None = None) -> None:
        exclude = exclude.resolve() if exclude else None
        while self._free_disk_percent() < self.min_free_disk_percent:
            victim = self._oldest_cache_dir(exclude=exclude)
            if not victim:
                return
            shutil.rmtree(victim, ignore_errors=True)
            logger.info(
                "cache_pruned path=%s free_disk_percent=%.2f min_free_disk_percent=%.2f",
                victim,
                self._free_disk_percent(),
                self.min_free_disk_percent,
            )

    def _free_disk_percent(self) -> float:
        usage = shutil.disk_usage(self.download_dir)
        if usage.total == 0:
            return 100.0
        return usage.free / usage.total * 100

    def _oldest_cache_dir(self, exclude: Path | None = None) -> Path | None:
        candidates: list[tuple[float, Path]] = []
        for child in self.download_dir.iterdir():
            if not child.is_dir() or child.name.endswith(".part"):
                continue
            if exclude and child.resolve() == exclude:
                continue
            metadata_path = child / "metadata.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                last_accessed = float(metadata.get("last_accessed_at", child.stat().st_mtime))
            except (OSError, json.JSONDecodeError, ValueError):
                last_accessed = child.stat().st_mtime
            candidates.append((last_accessed, child))

        if not candidates:
            return None

        return min(candidates, key=lambda item: item[0])[1]
