import re
from urllib.parse import parse_qs, urlparse, urlunparse


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "np.reddit.com",
    "m.reddit.com",
    "sh.reddit.com",
    "redd.it",
}

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def extract_supported_links(text: str) -> list[str]:
    links: list[str] = []
    for match in URL_RE.finditer(text or ""):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        normalized = normalize_youtube_url(candidate) or normalize_reddit_url(candidate)
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


def normalize_reddit_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in REDDIT_HOSTS:
        return None

    if host == "redd.it":
        post_id = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[a-z0-9]{5,}", post_id, re.IGNORECASE):
            return urlunparse(("https", "www.reddit.com", f"/comments/{post_id}/", "", "", ""))
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 4 and path_parts[0] == "r" and path_parts[2] == "comments":
        subreddit = path_parts[1]
        post_id = path_parts[3]
        slug = path_parts[4] if len(path_parts) >= 5 else ""
        if re.fullmatch(r"[A-Za-z0-9_]+", subreddit) and re.fullmatch(r"[a-z0-9]{5,}", post_id, re.IGNORECASE):
            path = f"/r/{subreddit}/comments/{post_id}/"
            if slug:
                path += f"{slug}/"
            return urlunparse(("https", "www.reddit.com", path, "", "", ""))

    if len(path_parts) >= 2 and path_parts[0] == "comments":
        post_id = path_parts[1]
        if re.fullmatch(r"[a-z0-9]{5,}", post_id, re.IGNORECASE):
            return urlunparse(("https", "www.reddit.com", f"/comments/{post_id}/", "", "", ""))

    return None
