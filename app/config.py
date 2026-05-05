import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    download_dir: str = "/tmp/videogram-downloads"
    max_download_mb: int = 48
    min_free_disk_percent: float = 5.0
    log_level: str = "INFO"

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024


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
    )
