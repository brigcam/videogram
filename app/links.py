import re
from urllib.parse import parse_qs, urlparse, urlunparse


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def extract_supported_links(text: str) -> list[str]:
    links: list[str] = []
    for match in URL_RE.finditer(text or ""):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        normalized = normalize_youtube_url(candidate)
        if normalized and normalized not in links:
            links.append(normalized)
    return links


def normalize_youtube_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS:
        return None

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    elif parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
        video_id = parsed.path.strip("/").split("/")[1]
    else:
        return None

    if not re.fullmatch(r"[\w-]{6,}", video_id):
        return None

    return urlunparse(("https", "www.youtube.com", "/watch", "", f"v={video_id}", ""))
