import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserCookieSite:
    site: str
    start_url: str
    cookie_url: str
    domains: tuple[str, ...]
    session_cookie_names: tuple[str, ...] = ()
    blocked_paths: tuple[str, ...] = ()


BROWSER_COOKIE_SITES = {
    "youtube": BrowserCookieSite(
        site="youtube",
        start_url="https://www.youtube.com/",
        cookie_url="https://www.youtube.com/",
        domains=("youtube.com", "google.com"),
        session_cookie_names=("SAPISID", "APISID", "SSID", "HSID", "SID", "LOGIN_INFO"),
        blocked_paths=("/signin", "/accounts/"),
    ),
    "reddit": BrowserCookieSite(
        site="reddit",
        start_url="https://www.reddit.com/",
        cookie_url="https://www.reddit.com/",
        domains=("reddit.com",),
        session_cookie_names=("reddit_session", "loid", "token_v2"),
        blocked_paths=("/login",),
    ),
    "instagram": BrowserCookieSite(
        site="instagram",
        start_url="https://www.instagram.com/",
        cookie_url="https://www.instagram.com/",
        domains=("instagram.com",),
        session_cookie_names=("sessionid",),
        blocked_paths=("/accounts/login", "/challenge", "/checkpoint", "/suspended"),
    ),
    "facebook": BrowserCookieSite(
        site="facebook",
        start_url="https://www.facebook.com/",
        cookie_url="https://www.facebook.com/",
        domains=("facebook.com",),
        session_cookie_names=("c_user", "xs"),
        blocked_paths=("/login", "/checkpoint"),
    ),
    "threads": BrowserCookieSite(
        site="threads",
        start_url="https://www.threads.com/",
        cookie_url="https://www.threads.com/",
        domains=("threads.com", "instagram.com"),
        session_cookie_names=("sessionid",),
        blocked_paths=("/login", "/challenge", "/checkpoint"),
    ),
    "x": BrowserCookieSite(
        site="x",
        start_url="https://x.com/",
        cookie_url="https://x.com/",
        domains=("x.com", "twitter.com"),
        session_cookie_names=("auth_token", "ct0"),
        blocked_paths=("/login", "/i/flow/login", "/account/access"),
    ),
    "tiktok": BrowserCookieSite(
        site="tiktok",
        start_url="https://www.tiktok.com/",
        cookie_url="https://www.tiktok.com/",
        domains=("tiktok.com",),
        session_cookie_names=("sessionid", "sid_tt", "sid_guard"),
        blocked_paths=("/login",),
    ),
}
SUPPORTED_BROWSER_COOKIE_SITES = set(BROWSER_COOKIE_SITES)
BROWSER_COOKIE_SITE_ALIASES = {"twitter": "x"}


@dataclass(frozen=True)
class BrowserCookieRefreshResult:
    site: str
    ok: bool
    message: str
    cookie_text: str = ""
    cookie_count: int = 0
    current_url: str = ""


class BrowserCookieRefresher:
    def __init__(self, profile_dir: str, cookies_dir: str, chromium_executable: str = "/usr/bin/chromium") -> None:
        self.profile_dir = Path(profile_dir)
        self.cookies_dir = Path(cookies_dir) if cookies_dir else Path("")
        self.chromium_executable = chromium_executable

    async def refresh(self, site: str, request_id: str) -> BrowserCookieRefreshResult:
        return await asyncio.to_thread(self._refresh_sync, site, request_id)

    def _refresh_sync(self, site: str, request_id: str) -> BrowserCookieRefreshResult:
        site = normalize_browser_cookie_site(site)
        site_config = BROWSER_COOKIE_SITES.get(site)
        if not site_config:
            return BrowserCookieRefreshResult(site, False, "Refresh browser non supportato per questo sito.")

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return BrowserCookieRefreshResult(
                site,
                False,
                "Playwright non e' installato nel container. Ricostruisci l'immagine.",
            )

        site_profile_dir = self.profile_dir / site
        site_profile_dir.mkdir(parents=True, exist_ok=True)
        cookie_file = self.cookies_dir / f"{site}.txt"
        seed_cookies = load_netscape_cookies(cookie_file)

        logger.info(
            "request_id=%s browser_cookie_refresh_start site=%s profile_dir=%s seed_cookie_count=%s",
            request_id,
            site,
            site_profile_dir,
            len(seed_cookies),
        )

        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(site_profile_dir),
                    executable_path=self.chromium_executable,
                    headless=True,
                    args=(
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ),
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1365, "height": 900},
                    locale="it-IT",
                    timezone_id="Europe/Rome",
                )
                try:
                    if seed_cookies:
                        context.add_cookies(seed_cookies)
                    page = context.new_page()
                    try:
                        page.goto(site_config.start_url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(5000)
                    except PlaywrightTimeoutError:
                        logger.warning("request_id=%s browser_cookie_refresh_navigation_timeout site=%s", request_id, site)

                    current_url = page.url
                    cookies = context.cookies(site_config.cookie_url)
                    site_cookies = filter_site_cookies(cookies, site_config.domains)
                    has_session = site_has_session_cookie(site_cookies, site_config.session_cookie_names)
                    blocked_state = blocked_state_for_url(current_url, site_config)

                    if not has_session:
                        return BrowserCookieRefreshResult(
                            site,
                            False,
                            f"Sessione {site} non valida o non presente: serve login manuale o nuovi cookie.",
                            current_url=current_url,
                        )
                    if blocked_state:
                        return BrowserCookieRefreshResult(
                            site,
                            False,
                            f"{site} richiede intervento manuale ({blocked_state}).",
                            current_url=current_url,
                        )

                    cookie_text = cookies_to_netscape(site_cookies)
                    logger.info(
                        "request_id=%s browser_cookie_refresh_complete site=%s cookie_count=%s current_url=%s",
                        request_id,
                        site,
                        len(site_cookies),
                        current_url,
                    )
                    return BrowserCookieRefreshResult(
                        site,
                        True,
                        "Cookie aggiornati dalla sessione browser.",
                        cookie_text=cookie_text,
                        cookie_count=len(site_cookies),
                        current_url=current_url,
                    )
                finally:
                    context.close()
        except Exception as exc:
            logger.warning("request_id=%s browser_cookie_refresh_failed site=%s error=%s", request_id, site, exc)
            return BrowserCookieRefreshResult(site, False, f"Refresh browser non riuscito: {exc}")


def normalize_browser_cookie_site(site: str) -> str:
    normalized = site.strip().lower()
    return BROWSER_COOKIE_SITE_ALIASES.get(normalized, normalized)


def blocked_state_for_url(url: str, site_config: BrowserCookieSite) -> str:
    lowered = (url or "").lower()
    for path in site_config.blocked_paths:
        if path in lowered:
            return path.rstrip("/").rsplit("/", 1)[-1] or path.strip("/")
    return ""


def instagram_blocked_state(url: str) -> str:
    return blocked_state_for_url(url, BROWSER_COOKIE_SITES["instagram"])


def filter_site_cookies(cookies: list[dict], domains: tuple[str, ...]) -> list[dict]:
    return [cookie for cookie in cookies if domain_matches(cookie.get("domain", ""), domains)]


def domain_matches(cookie_domain: str, domains: tuple[str, ...]) -> bool:
    normalized = cookie_domain.lstrip(".").lower()
    return any(normalized == domain or normalized.endswith(f".{domain}") for domain in domains)


def site_has_session_cookie(cookies: list[dict], session_cookie_names: tuple[str, ...]) -> bool:
    if not session_cookie_names:
        return bool(cookies)
    wanted = {name.lower() for name in session_cookie_names}
    return any(cookie.get("name", "").lower() in wanted and cookie.get("value") for cookie in cookies)


def cookies_to_netscape(cookies: list[dict]) -> str:
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by Videogram browser refresh",
    ]
    for cookie in sorted(cookies, key=lambda item: (item.get("domain", ""), item.get("path", ""), item.get("name", ""))):
        domain = cookie.get("domain") or ""
        if not domain:
            continue
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = cookie.get("path") or "/"
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires = int(cookie.get("expires") or 0)
        if expires < 0:
            expires = 0
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if not name:
            continue
        if cookie.get("httpOnly"):
            domain = f"#HttpOnly_{domain}"
        lines.append("\t".join((domain, include_subdomains, path, secure, str(expires), name, value)))
    return "\n".join(lines).rstrip() + "\n"


def load_netscape_cookies(path: Path) -> list[dict]:
    if not path or not path.exists() or not path.is_file():
        return []

    cookies: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("# Netscape HTTP Cookie File"):
            continue
        http_only = False
        if stripped.startswith("#HttpOnly_"):
            stripped = stripped.removeprefix("#HttpOnly_")
            http_only = True
        elif stripped.startswith("#"):
            continue

        fields = stripped.split("\t")
        if len(fields) < 7:
            fields = re.split(r"\s+", stripped, maxsplit=6)
        if len(fields) < 7:
            continue

        domain, _include_subdomains, path_value, secure, expires, name, value = fields[:7]
        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path_value or "/",
            "httpOnly": http_only,
            "secure": secure.upper() == "TRUE",
            "sameSite": "Lax",
        }
        try:
            expires_value = int(float(expires))
        except ValueError:
            expires_value = 0
        cookie["expires"] = expires_value if expires_value > int(time.time()) else -1
        cookies.append(cookie)
    return cookies
