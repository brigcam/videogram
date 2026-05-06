import asyncio
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.transcripts import Transcript


logger = logging.getLogger(__name__)


class SummaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SummaryResult:
    text: str
    cached: bool = False


class OpenAISummarizer:
    def __init__(
        self,
        api_key: str,
        model: str,
        prompt: str,
        max_transcript_chars: int,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.prompt = prompt
        self.max_transcript_chars = max_transcript_chars

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def summarize(
        self,
        title: str,
        source_url: str,
        transcript: Transcript,
        cache_dir: Path,
        request_id: str,
    ) -> SummaryResult | None:
        if not self.enabled:
            return None
        return await asyncio.to_thread(
            self._summarize_sync,
            title,
            source_url,
            transcript,
            cache_dir,
            request_id,
        )

    def _summarize_sync(
        self,
        title: str,
        source_url: str,
        transcript: Transcript,
        cache_dir: Path,
        request_id: str,
    ) -> SummaryResult:
        transcript_text = transcript.text[: self.max_transcript_chars]
        parameters = self._summary_parameters(transcript_text)
        cached = self._load_cached_summary(cache_dir, parameters)
        if cached:
            logger.info("request_id=%s summary_cache_hit chars=%s", request_id, len(cached))
            return SummaryResult(cached, cached=True)

        logger.info(
            "request_id=%s summary_start model=%s transcript_chars=%s language=%s source=%s",
            request_id,
            self.model,
            len(transcript_text),
            transcript.language,
            transcript.source,
        )
        input_text = (
            f"Titolo: {title}\n"
            f"URL: {source_url}\n"
            f"Lingua trascrizione: {transcript.language}\n"
            f"Tipo trascrizione: {transcript.source}\n\n"
            f"Trascrizione:\n{transcript_text}"
        )
        payload = {
            "model": self.model,
            "instructions": self.prompt,
            "input": input_text,
            "store": False,
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started_at = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            logger.warning("request_id=%s summary_openai_http_failed status=%s body=%s", request_id, exc.code, error_body)
            raise SummaryError(f"OpenAI API returned HTTP {exc.code}") from exc
        except Exception as exc:
            logger.warning("request_id=%s summary_openai_failed error=%s", request_id, exc)
            raise SummaryError(str(exc)) from exc

        summary = self._extract_output_text(data)
        if not summary:
            raise SummaryError("OpenAI API response did not contain output text.")

        self._write_cached_summary(cache_dir, parameters, summary)
        logger.info(
            "request_id=%s summary_complete elapsed_ms=%s chars=%s",
            request_id,
            int((time.perf_counter() - started_at) * 1000),
            len(summary),
        )
        return SummaryResult(summary)

    def _summary_parameters(self, transcript_text: str) -> dict:
        parameters = {
            "model": self.model,
            "prompt": self.prompt,
            "max_transcript_chars": self.max_transcript_chars,
            "openai_api_key_sha256": hashlib.sha256(self.api_key.encode("utf-8")).hexdigest(),
            "transcript_sha256": hashlib.sha256(transcript_text.encode("utf-8")).hexdigest(),
        }
        parameters["parameters_sha256"] = hashlib.sha256(
            json.dumps(parameters, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return parameters

    def _cache_key(self, transcript_text: str) -> str:
        return self._summary_parameters(transcript_text)["parameters_sha256"]

    def _load_cached_summary(self, cache_dir: Path, parameters: dict) -> str | None:
        metadata_path = cache_dir / "summary.json"
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        cached_parameters = metadata.get("parameters")
        if not isinstance(cached_parameters, dict):
            if metadata.get("cache_key") != parameters["parameters_sha256"]:
                return None
        elif cached_parameters != parameters:
            return None
        summary = metadata.get("summary")
        return summary if isinstance(summary, str) and summary.strip() else None

    def _write_cached_summary(self, cache_dir: Path, parameters: dict, summary: str) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "cache_key": parameters["parameters_sha256"],
            "parameters": parameters,
            "model": self.model,
            "created_at": time.time(),
            "summary": summary,
        }
        (cache_dir / "summary.parameters.json").write_text(json.dumps(parameters, indent=2), encoding="utf-8")
        (cache_dir / "summary.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _extract_output_text(self, data: dict) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        parts: list[str] = []
        for item in data.get("output") or []:
            for content in item.get("content") or []:
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
