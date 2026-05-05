import asyncio
import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL


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
    def __init__(self, download_dir: str, max_bytes: int, min_free_disk_percent: float) -> None:
        self.download_dir = Path(download_dir)
        self.max_bytes = max_bytes
        self.min_free_disk_percent = min_free_disk_percent
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def download(self, url: str) -> DownloadedVideo:
        return await asyncio.to_thread(self._download_sync, url)

    def remove(self, path: Path) -> None:
        parent = path.parent
        if parent.exists() and parent.parent == self.download_dir:
            shutil.rmtree(parent, ignore_errors=True)

    def _download_sync(self, url: str) -> DownloadedVideo:
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_dir = self.download_dir / cache_key
        cached = self._load_cached_video(cache_dir, url)
        if cached:
            self._touch_cache(cache_dir)
            return cached

        self._prune_cache()

        temp_dir = self.download_dir / f"{uuid.uuid4().hex}.part"
        temp_dir.mkdir(parents=True, exist_ok=True)

        options = {
            "format": "bv*[ext=mp4][filesize<={0}]+ba[ext=m4a]/b[ext=mp4][filesize<={0}]/best[filesize<={0}]".format(
                self.max_bytes
            ),
            "outtmpl": str(temp_dir / "%(title).180B [%(id)s].%(ext)s"),
            "merge_output_format": "mp4",
            "max_filesize": self.max_bytes,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloadError(str(exc)) from exc

        path = Path(filename)
        if not path.exists():
            mp4_files = sorted(temp_dir.glob("*.mp4"), key=lambda item: item.stat().st_mtime)
            if not mp4_files:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise DownloadError("Download completed, but no video file was produced.")
            path = mp4_files[-1]

        if path.stat().st_size > self.max_bytes:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloadError("The downloaded video is larger than the configured limit.")

        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_dir.rename(cache_dir)
        cached_path = cache_dir / path.name
        title = info.get("title") or "Video"
        self._write_metadata(cache_dir, url, title, cached_path.name)
        self._prune_cache(exclude=cache_dir)
        delete_after_send = self._free_disk_percent() < self.min_free_disk_percent

        return DownloadedVideo(
            path=cached_path,
            title=title,
            source_url=url,
            delete_after_send=delete_after_send,
        )

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
