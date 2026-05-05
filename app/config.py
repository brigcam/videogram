import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    download_dir: str = "/tmp/videogram-downloads"
    max_download_mb: int = 48
    min_free_disk_percent: float = 5.0
    log_level: str = "INFO"
    log_file: str = "/var/log/videogram/videogram.log"
    log_max_mb: int = 10
    log_backup_count: int = 5
    ytdlp_cookies_file: str = ""

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def log_max_bytes(self) -> int:
        return self.log_max_mb * 1024 * 1024


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN. Create .env from .env.example.")

    return Settings(
        telegram_bot_token=token,
        download_dir=os.getenv("DOWNLOAD_DIR", "/tmp/videogram-downloads"),
        max_download_mb=int(os.getenv("MAX_DOWNLOAD_MB", "48")),
        min_free_disk_percent=float(os.getenv("MIN_FREE_DISK_PERCENT", "5")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "/var/log/videogram/videogram.log"),
        log_max_mb=int(os.getenv("LOG_MAX_MB", "10")),
        log_backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5")),
        ytdlp_cookies_file=os.getenv("YTDLP_COOKIES_FILE", "").strip(),
    )
