import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_chat_ids: frozenset[int] = frozenset()
    allowed_user_ids: frozenset[int] = frozenset()
    openai_api_key: str = ""
    openai_summary_model: str = "gpt-5.2"
    openai_summary_prompt: str = (
        "Riassumi la trascrizione del video in italiano, in modo utile e conciso. "
        "Evidenzia i punti principali e conserva eventuali dettagli concreti importanti."
    )
    openai_summary_max_transcript_chars: int = 20000
    summary_transcript_langs: tuple[str, ...] = ("it", "en")
    download_dir: str = "/tmp/videogram-downloads"
    max_download_mb: int = 512
    max_telegram_upload_mb: int = 48
    min_free_disk_percent: float = 5.0
    log_level: str = "INFO"
    log_file: str = "/var/log/videogram/videogram.log"
    log_max_mb: int = 10
    log_backup_count: int = 5
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_dir: str = ""

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def max_telegram_upload_bytes(self) -> int:
        return self.max_telegram_upload_mb * 1024 * 1024

    @property
    def log_max_bytes(self) -> int:
        return self.log_max_mb * 1024 * 1024


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN. Create .env from .env.example.")

    return Settings(
        telegram_bot_token=token,
        allowed_chat_ids=parse_allowed_chat_ids(os.getenv("ALLOWED_CHAT_IDS", "")),
        allowed_user_ids=parse_allowed_user_ids(os.getenv("ALLOWED_USER_IDS", "")),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_summary_model=os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.2").strip() or "gpt-5.2",
        openai_summary_prompt=os.getenv(
            "OPENAI_SUMMARY_PROMPT",
            Settings.openai_summary_prompt,
        ).strip()
        or Settings.openai_summary_prompt,
        openai_summary_max_transcript_chars=int(os.getenv("OPENAI_SUMMARY_MAX_TRANSCRIPT_CHARS", "20000")),
        summary_transcript_langs=parse_string_list(os.getenv("SUMMARY_TRANSCRIPT_LANGS", "it,en")),
        download_dir=os.getenv("DOWNLOAD_DIR", "/tmp/videogram-downloads"),
        max_download_mb=int(os.getenv("MAX_DOWNLOAD_MB", "512")),
        max_telegram_upload_mb=int(os.getenv("MAX_TELEGRAM_UPLOAD_MB", "48")),
        min_free_disk_percent=float(os.getenv("MIN_FREE_DISK_PERCENT", "5")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "/var/log/videogram/videogram.log"),
        log_max_mb=int(os.getenv("LOG_MAX_MB", "10")),
        log_backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5")),
        ytdlp_cookies_file=os.getenv("YTDLP_COOKIES_FILE", "").strip(),
        ytdlp_cookies_dir=os.getenv("YTDLP_COOKIES_DIR", "").strip(),
    )


def parse_allowed_chat_ids(raw_value: str) -> frozenset[int]:
    return parse_id_list(raw_value, "ALLOWED_CHAT_IDS")


def parse_allowed_user_ids(raw_value: str) -> frozenset[int]:
    return parse_id_list(raw_value, "ALLOWED_USER_IDS")


def parse_id_list(raw_value: str, env_name: str) -> frozenset[int]:
    raw_value = raw_value.strip()
    if not raw_value:
        return frozenset()

    ids = set()
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.add(int(item))
        except ValueError as exc:
            raise RuntimeError(f"Invalid {env_name} entry: {item!r}") from exc
    return frozenset(ids)


def parse_string_list(raw_value: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return values or ("it", "en")
