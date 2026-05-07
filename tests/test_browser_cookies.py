import tempfile
import unittest
from pathlib import Path

from app.browser_cookies import (
    SUPPORTED_BROWSER_COOKIE_SITES,
    cookies_to_netscape,
    domain_matches,
    instagram_blocked_state,
    load_netscape_cookies,
    normalize_browser_cookie_site,
    site_has_session_cookie,
)


class BrowserCookieTests(unittest.TestCase):
    def test_detects_instagram_blocked_states(self) -> None:
        self.assertEqual(instagram_blocked_state("https://www.instagram.com/accounts/login/"), "login")
        self.assertEqual(instagram_blocked_state("https://www.instagram.com/challenge/"), "challenge")
        self.assertEqual(instagram_blocked_state("https://www.instagram.com/"), "")

    def test_supports_cookie_sites_used_by_downloader(self) -> None:
        self.assertEqual(
            SUPPORTED_BROWSER_COOKIE_SITES,
            {"youtube", "reddit", "instagram", "facebook", "threads", "x", "tiktok"},
        )
        self.assertEqual(normalize_browser_cookie_site("twitter"), "x")

    def test_domain_matching_handles_subdomains(self) -> None:
        self.assertTrue(domain_matches(".www.youtube.com", ("youtube.com",)))
        self.assertTrue(domain_matches(".google.com", ("google.com",)))
        self.assertFalse(domain_matches(".example.com", ("youtube.com",)))

    def test_site_has_session_cookie_checks_known_names(self) -> None:
        cookies = [{"name": "auth_token", "value": "abc"}]

        self.assertTrue(site_has_session_cookie(cookies, ("auth_token",)))
        self.assertFalse(site_has_session_cookie(cookies, ("sessionid",)))

    def test_converts_cookies_to_netscape(self) -> None:
        text = cookies_to_netscape(
            [
                {
                    "domain": ".instagram.com",
                    "path": "/",
                    "secure": True,
                    "expires": 1893456000,
                    "name": "sessionid",
                    "value": "abc",
                    "httpOnly": True,
                }
            ]
        )

        self.assertIn("#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc", text)

    def test_loads_netscape_cookies_for_playwright(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_path = Path(temp_dir) / "instagram.txt"
            cookie_path.write_text(
                "# Netscape HTTP Cookie File\n"
                "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tabc\n",
                encoding="utf-8",
            )

            cookies = load_netscape_cookies(cookie_path)

            self.assertEqual(cookies[0]["name"], "sessionid")
            self.assertEqual(cookies[0]["domain"], ".instagram.com")
            self.assertTrue(cookies[0]["httpOnly"])


if __name__ == "__main__":
    unittest.main()
