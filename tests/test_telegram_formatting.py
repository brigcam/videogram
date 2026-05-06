import unittest

from app.telegram_formatting import normalize_markdown_lists, summary_markdown_to_telegram_html


class TelegramFormattingTests(unittest.TestCase):
    def test_converts_markdown_bullets_and_bold(self) -> None:
        self.assertEqual(
            summary_markdown_to_telegram_html("- **Titolo**: testo"),
            "• <b>Titolo</b>: testo",
        )

    def test_converts_headings_to_bold(self) -> None:
        self.assertEqual(summary_markdown_to_telegram_html("## Sezione"), "<b>Sezione</b>")

    def test_escapes_html(self) -> None:
        self.assertEqual(summary_markdown_to_telegram_html("- **A < B**"), "• <b>A &lt; B</b>")

    def test_normalizes_numbered_lists(self) -> None:
        self.assertEqual(normalize_markdown_lists("1) primo"), "1. primo")


if __name__ == "__main__":
    unittest.main()
