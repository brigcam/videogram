import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailureRecorder:
    path: Path
    max_error_chars: int = 1000

    def record(
        self,
        *,
        request_id: str,
        url: str,
        stage: str,
        error: Exception,
        chat_type: str = "",
    ) -> None:
        if not self.path:
            return
        record = {
            "created_at": time.time(),
            "request_id": request_id,
            "stage": stage,
            "platform": urlparse(url).netloc.lower(),
            "url": url,
            "chat_type": chat_type,
            "error_type": type(error).__name__,
            "error": self._clean_error(str(error)),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                output.write("\n")
        except OSError as exc:
            logger.warning("failed_link_record_write_failed path=%s error=%s", self.path, exc)

    def _clean_error(self, error: str) -> str:
        cleaned = " ".join((error or "").split())
        if len(cleaned) <= self.max_error_chars:
            return cleaned
        return f"{cleaned[: self.max_error_chars].rstrip()}..."
