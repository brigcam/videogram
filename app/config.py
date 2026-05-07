import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_api_base_url: str = ""
    telegram_api_file_base_url: str = ""
    telegram_local_mode: bool = False
    allowed_chat_ids: frozenset[int] = frozenset()
    allowed_user_ids: frozenset[int] = frozenset()
    usage_allowed_user_ids: frozenset[int] = frozenset()
    cookie_allowed_user_ids: frozenset[int] = frozenset()
    usage_report_user_id: int = 0
    usage_check_interval_minutes: int = 60
    usage_alert_step_percent: int = 10
    usage_alert_state_file: str = "/var/log/videogram/usage-alerts.json"
    hetzner_api_token: str = ""
    hetzner_server_id: str = ""
    hetzner_monthly_traffic_tb: float = 20.0
    openai_admin_key: str = ""
    openai_monthly_budget_usd: float = 0.0
    openai_api_key: str = ""
    openai_summary_model: str = "gpt-5-mini"
    openai_summary_prompt: str = (
        "Riassumi il contenuto in italiano, in modo utile e conciso. "
        "Evidenzia i punti principali e conserva eventuali dettagli concreti importanti."
    )
    openai_summary_max_transcript_chars: int = 20000
    summary_transcript_langs: tuple[str, ...] = ("it", "en")
    download_dir: str = "/tmp/videogram-downloads"
    max_download_mb: int = 512
    max_telegram_upload_mb: int = 48
    min_free_disk_percent: float = 5.0
    max_concurrent_jobs: int = 2
    site_concurrent_jobs: int = 1
    log_level: str = "INFO"
    log_file: str = "/var/log/videogram/videogram.log"
    log_max_mb: int = 10
    log_backup_count: int = 5
    failed_links_file: str = "/var/log/videogram/failed-links.jsonl"
    ytdlp_cookies_file: str = ""
    ytdlp_cookies_dir: str = ""
    browser_profile_dir: str = "/browser-profiles"
    browser_chromium_executable: str = "/usr/bin/chromium"

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
        telegram_api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "").strip(),
        telegram_api_file_base_url=os.getenv("TELEGRAM_API_FILE_BASE_URL", "").strip(),
        telegram_local_mode=parse_bool(os.getenv("TELEGRAM_LOCAL_MODE", "false")),
        allowed_chat_ids=parse_allowed_chat_ids(os.getenv("ALLOWED_CHAT_IDS", "")),
        allowed_user_ids=parse_allowed_user_ids(os.getenv("ALLOWED_USER_IDS", "")),
        usage_allowed_user_ids=parse_allowed_user_ids(os.getenv("USAGE_ALLOWED_USER_IDS", "")),
        cookie_allowed_user_ids=parse_allowed_user_ids(os.getenv("COOKIE_ALLOWED_USER_IDS", "")),
        usage_report_user_id=parse_optional_int(os.getenv("USAGE_REPORT_USER_ID", "")),
        usage_check_interval_minutes=max(1, int(os.getenv("USAGE_CHECK_INTERVAL_MINUTES", "60"))),
        usage_alert_step_percent=min(100, max(1, int(os.getenv("USAGE_ALERT_STEP_PERCENT", "10")))),
        usage_alert_state_file=os.getenv("USAGE_ALERT_STATE_FILE", "/var/log/videogram/usage-alerts.json"),
        hetzner_api_token=os.getenv("HETZNER_API_TOKEN", "").strip(),
        hetzner_server_id=os.getenv("HETZNER_SERVER_ID", "").strip(),
        hetzner_monthly_traffic_tb=float(os.getenv("HETZNER_MONTHLY_TRAFFIC_TB", "20")),
        openai_admin_key=os.getenv("OPENAI_ADMIN_KEY", "").strip(),
        openai_monthly_budget_usd=float(os.getenv("OPENAI_MONTHLY_BUDGET_USD", "0")),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_summary_model=os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5-mini").strip() or "gpt-5-mini",
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
        max_concurrent_jobs=max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "2"))),
        site_concurrent_jobs=max(1, int(os.getenv("SITE_CONCURRENT_JOBS", "1"))),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "/var/log/videogram/videogram.log"),
        log_max_mb=int(os.getenv("LOG_MAX_MB", "10")),
        log_backup_count=int(os.getenv("LOG_BACKUP_COUNT", "5")),
        failed_links_file=os.getenv("FAILED_LINKS_FILE", "/var/log/videogram/failed-links.jsonl"),
        ytdlp_cookies_file=os.getenv("YTDLP_COOKIES_FILE", "").strip(),
        ytdlp_cookies_dir=os.getenv("YTDLP_COOKIES_DIR", "").strip(),
        browser_profile_dir=os.getenv("BROWSER_PROFILE_DIR", "/browser-profiles"),
        browser_chromium_executable=os.getenv("BROWSER_CHROMIUM_EXECUTABLE", "/usr/bin/chromium"),
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


def parse_optional_int(raw_value: str) -> int:
    raw_value = raw_value.strip()
    if not raw_value:
        return 0
    return int(raw_value)


def parse_string_list(raw_value: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return values or ("it", "en")


def parse_bool(raw_value: str) -> bool:
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}
