import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


SUPPORTED_BROWSER_COOKIE_SITES = {"instagram"}


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
        site = site.strip().lower()
        if site not in SUPPORTED_BROWSER_COOKIE_SITES:
            return BrowserCookieRefreshResult(site, False, "Refresh browser supportato solo per instagram.")

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
                        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(5000)
                    except PlaywrightTimeoutError:
                        logger.warning("request_id=%s browser_cookie_refresh_navigation_timeout site=%s", request_id, site)

                    current_url = page.url
                    cookies = context.cookies("https://www.instagram.com/")
                    instagram_cookies = [cookie for cookie in cookies if "instagram.com" in cookie.get("domain", "")]
                    has_session = any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in instagram_cookies)
                    blocked_state = instagram_blocked_state(current_url)

                    if not has_session:
                        return BrowserCookieRefreshResult(
                            site,
                            False,
                            "Sessione Instagram non valida o non presente: serve login manuale o nuovi cookie.",
                            current_url=current_url,
                        )
                    if blocked_state:
                        return BrowserCookieRefreshResult(
                            site,
                            False,
                            f"Instagram richiede intervento manuale ({blocked_state}).",
                            current_url=current_url,
                        )

                    cookie_text = cookies_to_netscape(instagram_cookies)
                    logger.info(
                        "request_id=%s browser_cookie_refresh_complete site=%s cookie_count=%s current_url=%s",
                        request_id,
                        site,
                        len(instagram_cookies),
                        current_url,
                    )
                    return BrowserCookieRefreshResult(
                        site,
                        True,
                        "Cookie aggiornati dalla sessione browser.",
                        cookie_text=cookie_text,
                        cookie_count=len(instagram_cookies),
                        current_url=current_url,
                    )
                finally:
                    context.close()
        except Exception as exc:
            logger.warning("request_id=%s browser_cookie_refresh_failed site=%s error=%s", request_id, site, exc)
            return BrowserCookieRefreshResult(site, False, f"Refresh browser non riuscito: {exc}")


def instagram_blocked_state(url: str) -> str:
    lowered = (url or "").lower()
    if "/accounts/login" in lowered:
        return "login"
    if "/challenge" in lowered:
        return "challenge"
    if "/checkpoint" in lowered:
        return "checkpoint"
    if "/suspended" in lowered:
        return "account sospeso"
    return ""


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
        }
        try:
            expires_value = int(float(expires))
        except ValueError:
            expires_value = 0
        cookie["expires"] = expires_value if expires_value > int(time.time()) else -1
        cookies.append(cookie)
    return cookies
