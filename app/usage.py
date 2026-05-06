import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


logger = logging.getLogger(__name__)


BYTES_PER_TB = 1024**4


@dataclass(frozen=True)
class HetznerUsage:
    configured: bool
    outgoing_bytes: int = 0
    monthly_limit_bytes: int = 0
    percent: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class OpenAIUsage:
    configured: bool
    cost_value: float = 0.0
    currency: str = "usd"
    error: str = ""


@dataclass(frozen=True)
class UsageSnapshot:
    hetzner: HetznerUsage
    openai: OpenAIUsage
    period_start: datetime
    period_end: datetime


class UsageMonitor:
    def __init__(
        self,
        *,
        hetzner_api_token: str = "",
        hetzner_server_id: str = "",
        hetzner_monthly_traffic_tb: float = 20.0,
        openai_admin_key: str = "",
        alert_step_percent: int = 10,
        alert_state_file: str = "/var/log/videogram/usage-alerts.json",
    ) -> None:
        self.hetzner_api_token = hetzner_api_token
        self.hetzner_server_id = hetzner_server_id
        self.hetzner_monthly_traffic_tb = hetzner_monthly_traffic_tb
        self.openai_admin_key = openai_admin_key
        self.alert_step_percent = alert_step_percent
        self.alert_state_file = Path(alert_state_file)

    async def snapshot(self) -> UsageSnapshot:
        return await asyncio.to_thread(self._snapshot_sync)

    def _snapshot_sync(self) -> UsageSnapshot:
        start = month_start_utc()
        end = datetime.now(UTC)
        return UsageSnapshot(
            hetzner=self._fetch_hetzner_usage(start, end),
            openai=self._fetch_openai_usage(start, end),
            period_start=start,
            period_end=end,
        )

    def _fetch_hetzner_usage(self, start: datetime, end: datetime) -> HetznerUsage:
        configured = bool(self.hetzner_api_token and self.hetzner_server_id)
        limit_bytes = int(self.hetzner_monthly_traffic_tb * BYTES_PER_TB)
        if not configured:
            return HetznerUsage(configured=False, monthly_limit_bytes=limit_bytes)
        try:
            query = urllib.parse.urlencode(
                {
                    "type": "network",
                    "start": start.isoformat().replace("+00:00", "Z"),
                    "end": end.isoformat().replace("+00:00", "Z"),
                    "step": "3600",
                }
            )
            url = f"https://api.hetzner.cloud/v1/servers/{self.hetzner_server_id}/metrics?{query}"
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.hetzner_api_token}"})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            outgoing_bytes = integrate_bandwidth_out(data)
            percent = outgoing_bytes / limit_bytes * 100 if limit_bytes else 0.0
            return HetznerUsage(
                configured=True,
                outgoing_bytes=outgoing_bytes,
                monthly_limit_bytes=limit_bytes,
                percent=percent,
            )
        except Exception as exc:
            logger.warning("hetzner_usage_fetch_failed error=%s", exc)
            return HetznerUsage(configured=True, monthly_limit_bytes=limit_bytes, error=str(exc))

    def _fetch_openai_usage(self, start: datetime, end: datetime) -> OpenAIUsage:
        if not self.openai_admin_key:
            return OpenAIUsage(configured=False)
        try:
            query = urllib.parse.urlencode(
                {
                    "start_time": int(start.timestamp()),
                    "end_time": int(end.timestamp()),
                    "limit": 31,
                }
            )
            request = urllib.request.Request(
                f"https://api.openai.com/v1/organization/costs?{query}",
                headers={"Authorization": f"Bearer {self.openai_admin_key}"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            total = 0.0
            currency = "usd"
            for bucket in data.get("data") or []:
                for result in bucket.get("results") or []:
                    amount = result.get("amount") or {}
                    total += float(amount.get("value") or 0)
                    currency = amount.get("currency") or currency
            return OpenAIUsage(configured=True, cost_value=total, currency=currency)
        except Exception as exc:
            logger.warning("openai_usage_fetch_failed error=%s", exc)
            return OpenAIUsage(configured=True, error=str(exc))

    def next_hetzner_alert_percent(self, percent: float) -> int:
        if percent <= 0:
            return 0
        crossed = int(percent // self.alert_step_percent) * self.alert_step_percent
        if crossed <= 0:
            return 0
        state = self._load_alert_state()
        last_sent = int(state.get("hetzner_last_alert_percent") or 0)
        return crossed if crossed > last_sent else 0

    def mark_hetzner_alert_sent(self, percent: int) -> None:
        state = self._load_alert_state()
        state["hetzner_last_alert_percent"] = percent
        state["hetzner_last_alert_at"] = time.time()
        try:
            self.alert_state_file.parent.mkdir(parents=True, exist_ok=True)
            self.alert_state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("usage_alert_state_write_failed path=%s error=%s", self.alert_state_file, exc)

    def _load_alert_state(self) -> dict:
        try:
            data = json.loads(self.alert_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def month_start_utc() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def integrate_bandwidth_out(data: dict) -> int:
    values = (
        data.get("metrics", {})
        .get("time_series", {})
        .get("network.0.bandwidth.out", {})
        .get("values")
        or []
    )
    samples: list[tuple[datetime, float]] = []
    for timestamp, value in values:
        try:
            parsed_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            samples.append((parsed_time, float(value)))
        except (TypeError, ValueError):
            continue
    if not samples:
        return 0

    total = 0.0
    for index, (timestamp, value) in enumerate(samples):
        if index + 1 < len(samples):
            seconds = max(0.0, (samples[index + 1][0] - timestamp).total_seconds())
        elif index > 0:
            seconds = max(0.0, (timestamp - samples[index - 1][0]).total_seconds())
        else:
            seconds = 0.0
        total += value * seconds
    return int(total)


def format_usage_report(snapshot: UsageSnapshot) -> str:
    lines = [
        "Utilizzo Videogram",
        f"Periodo: {snapshot.period_start:%Y-%m-%d} - {snapshot.period_end:%Y-%m-%d %H:%M} UTC",
        "",
        format_hetzner_usage(snapshot.hetzner),
        "",
        format_openai_usage(snapshot.openai),
    ]
    return "\n".join(lines)


def format_hetzner_usage(usage: HetznerUsage) -> str:
    if not usage.configured:
        return "Hetzner: non configurato."
    if usage.error:
        return f"Hetzner: errore lettura metriche ({usage.error})."
    return (
        "Hetzner outbound: "
        f"{format_bytes(usage.outgoing_bytes)} / {format_bytes(usage.monthly_limit_bytes)} "
        f"({usage.percent:.1f}%)."
    )


def format_openai_usage(usage: OpenAIUsage) -> str:
    if not usage.configured:
        return "OpenAI: monitor costi non configurato."
    if usage.error:
        return f"OpenAI: errore lettura costi ({usage.error})."
    return f"OpenAI costi mese: {usage.cost_value:.4f} {usage.currency.upper()}."


def format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


async def usage_alert_loop(application, interval_minutes: int, report_user_id: int) -> None:
    monitor: UsageMonitor | None = application.bot_data.get("usage_monitor")
    if not monitor or not report_user_id:
        return
    await asyncio.sleep(30)
    while True:
        try:
            snapshot = await monitor.snapshot()
            alert_percent = monitor.next_hetzner_alert_percent(snapshot.hetzner.percent)
            if alert_percent:
                await application.bot.send_message(
                    chat_id=report_user_id,
                    text=(
                        f"Soglia traffico Hetzner raggiunta: {alert_percent}%.\n\n"
                        f"{format_usage_report(snapshot)}"
                    ),
                )
                monitor.mark_hetzner_alert_sent(alert_percent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("usage_alert_loop_failed error=%s", exc)
        await asyncio.sleep(interval_minutes * 60)
