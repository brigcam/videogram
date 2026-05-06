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

INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
}

FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "mobile.facebook.com",
    "fb.watch",
}

THREADS_HOSTS = {
    "threads.net",
    "www.threads.net",
    "threads.com",
    "www.threads.com",
}

X_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}

TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def extract_supported_links(text: str) -> list[str]:
    links: list[str] = []
    normalizers = (
        normalize_youtube_url,
        normalize_reddit_url,
        normalize_instagram_url,
        normalize_facebook_url,
        normalize_threads_url,
        normalize_x_url,
        normalize_tiktok_url,
    )
    for match in URL_RE.finditer(text or ""):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        normalized = next((normalized for normalize in normalizers if (normalized := normalize(candidate))), None)
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


def normalize_instagram_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in INSTAGRAM_HOSTS:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] in {"p", "reel", "tv"}:
        shortcode = path_parts[1]
        if re.fullmatch(r"[\w-]{5,}", shortcode):
            return urlunparse(("https", "www.instagram.com", f"/{path_parts[0]}/{shortcode}/", "", "", ""))

    if len(path_parts) >= 3 and path_parts[0] == "stories":
        username = path_parts[1]
        story_id = path_parts[2]
        if re.fullmatch(r"[\w.]{1,30}", username) and re.fullmatch(r"\d{5,}", story_id):
            return urlunparse(("https", "www.instagram.com", f"/stories/{username}/{story_id}/", "", "", ""))

    return None


def normalize_facebook_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in FACEBOOK_HOSTS:
        return None

    if host == "fb.watch":
        short_id = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[\w-]{4,}", short_id):
            return urlunparse(("https", "fb.watch", f"/{short_id}/", "", "", ""))
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    video_id = parse_qs(parsed.query).get("v", [""])[0]
    if parsed.path == "/watch/" or parsed.path == "/watch":
        if re.fullmatch(r"\d{5,}", video_id):
            return urlunparse(("https", "www.facebook.com", "/watch/", "", f"v={video_id}", ""))
        return None

    if len(path_parts) >= 2 and path_parts[0] in {"reel", "share"}:
        share_type = path_parts[1] if path_parts[0] == "share" else ""
        if path_parts[0] == "reel" and re.fullmatch(r"\d{5,}", path_parts[1]):
            return urlunparse(("https", "www.facebook.com", f"/reel/{path_parts[1]}/", "", "", ""))
        if share_type in {"r", "v"} and len(path_parts) >= 3 and re.fullmatch(r"[\w-]{4,}", path_parts[2]):
            return urlunparse(("https", "www.facebook.com", f"/share/{share_type}/{path_parts[2]}/", "", "", ""))

    if len(path_parts) >= 3 and path_parts[-2] in {"videos", "video"} and re.fullmatch(r"\d{5,}", path_parts[-1]):
        return urlunparse(("https", "www.facebook.com", "/" + "/".join(path_parts) + "/", "", "", ""))

    return None


def normalize_threads_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in THREADS_HOSTS:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 3 and path_parts[0].startswith("@") and path_parts[1] in {"post", "t"}:
        username = path_parts[0][1:]
        post_id = path_parts[2]
        if re.fullmatch(r"[\w.]{1,30}", username) and re.fullmatch(r"[\w-]{5,}", post_id):
            return urlunparse(("https", "www.threads.net", f"/@{username}/{path_parts[1]}/{post_id}/", "", "", ""))

    if len(path_parts) >= 2 and path_parts[0] in {"t", "post"}:
        post_id = path_parts[1]
        if re.fullmatch(r"[\w-]{5,}", post_id):
            return urlunparse(("https", "www.threads.net", f"/{path_parts[0]}/{post_id}/", "", "", ""))

    return None


def normalize_x_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in X_HOSTS:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 3 and path_parts[1] == "status":
        username = path_parts[0]
        status_id = path_parts[2]
        if re.fullmatch(r"[A-Za-z0-9_]{1,15}", username) and re.fullmatch(r"\d{5,}", status_id):
            return urlunparse(("https", "x.com", f"/{username}/status/{status_id}", "", "", ""))

    return None


def normalize_tiktok_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in TIKTOK_HOSTS:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        short_id = path_parts[0] if path_parts else ""
        if re.fullmatch(r"[\w-]{4,}", short_id):
            return urlunparse(("https", host, f"/{short_id}/", "", "", ""))
        return None

    if len(path_parts) >= 3 and path_parts[0].startswith("@") and path_parts[1] == "video":
        username = path_parts[0][1:]
        video_id = path_parts[2]
        if re.fullmatch(r"[\w.]{1,30}", username) and re.fullmatch(r"\d{5,}", video_id):
            return urlunparse(("https", "www.tiktok.com", f"/@{username}/video/{video_id}", "", "", ""))

    if len(path_parts) >= 2 and path_parts[0] in {"t", "v"}:
        video_id = path_parts[1]
        if re.fullmatch(r"[\w-]{4,}", video_id):
            return urlunparse(("https", "www.tiktok.com", f"/{path_parts[0]}/{video_id}/", "", "", ""))

    return None
