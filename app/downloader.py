import asyncio
import hashlib
import json
import logging
import shutil
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.transcripts import TRANSCRIPT_CACHE_VERSION, Transcript, parse_subtitle_text
from yt_dlp import YoutubeDL


logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedVideo:
    path: Path
    title: str
    source_url: str
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
        parent = path.parent
        if parent.exists() and parent.parent == self.download_dir:
            logger.info("request cleanup removing_cache_dir path=%s", parent)
            shutil.rmtree(parent, ignore_errors=True)

    def _download_sync(self, url: str, request_id: str) -> DownloadedVideo:
        started_at = time.perf_counter()
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

        temp_dir = self.download_dir / f"{uuid.uuid4().hex}.part"
        temp_dir.mkdir(parents=True, exist_ok=True)
        format_max_bytes = min(self.max_download_bytes, self.max_telegram_upload_bytes)

        options = {
            "format": (
                "bv*[ext=mp4][filesize<={0}]+ba[ext=m4a]/"
                "bv*[ext=mp4][filesize_approx<={0}]+ba[ext=m4a]/"
                "b[ext=mp4][filesize<={0}]/"
                "b[ext=mp4][filesize_approx<={0}]/"
                "bv*[ext=mp4]+ba[ext=m4a]/"
                "b[ext=mp4]/"
                "best"
            ).format(format_max_bytes),
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
            logger.info("request_id=%s download_start url=%s", request_id, url)
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
            raise DownloadError("The downloaded video is larger than the configured limit.")

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
            raise DownloadError("The downloaded video is larger than the Telegram upload limit.")

        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_dir.rename(cache_dir)
        cached_path = cache_dir / path.name
        title = info.get("title") or "Video"
        self._write_metadata(cache_dir, url, title, cached_path.name)
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
            delete_after_send=delete_after_send,
        )

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
            logger.info("request_id=%s transcript_extract_start url=%s", request_id, url)
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
            transcript = Transcript(text=transcript_text, language=language, source=source)
            self._write_cached_transcript(cache_dir, transcript, preferred_langs)
            return transcript
        except Exception as exc:
            logger.warning("request_id=%s transcript_extract_failed url=%s error=%s", request_id, url, exc)
            return None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
        transcript = Transcript.from_dict(metadata.get("transcript") or {})
        if not transcript:
            return None
        return transcript

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
            cached=True,
        )

    def _write_metadata(self, cache_dir: Path, url: str, title: str, filename: str) -> None:
        now = time.time()
        metadata = {
            "source_url": url,
            "title": title,
            "filename": filename,
            "created_at": now,
            "last_accessed_at": now,
        }
        (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _touch_cache(self, cache_dir: Path) -> None:
        metadata_path = cache_dir / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["last_accessed_at"] = time.time()
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
