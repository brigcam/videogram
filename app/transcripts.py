import html
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    source: str


def clean_transcript(text: str) -> str:
    lines = []
    previous = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == previous:
            continue
        previous = line
        lines.append(line)
    return "\n".join(lines).strip()


def parse_subtitle_text(raw_text: str, ext: str) -> str:
    ext = ext.lower()
    if ext == "json3":
        return parse_json3(raw_text)
    if ext in {"vtt", "srv3", "ttml"}:
        return parse_timed_text(raw_text)
    return clean_transcript(raw_text)


def parse_json3(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return ""

    parts = []
    for event in payload.get("events") or []:
        segments = event.get("segs") or []
        text = "".join(segment.get("utf8", "") for segment in segments)
        text = text.replace("\\n", " ").replace("\n", " ").strip()
        if text:
            parts.append(text)
    return clean_transcript("\n".join(parts))


def parse_timed_text(raw_text: str) -> str:
    lines = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:")):
            continue
        if "-->" in line:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = html.unescape(line).strip()
        if line:
            lines.append(line)
    return clean_transcript("\n".join(lines))
