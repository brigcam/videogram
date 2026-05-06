import html
import re


def summary_markdown_to_telegram_html(text: str) -> str:
    normalized = normalize_markdown_lists(text.strip())
    escaped = html.escape(normalized)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)
    return escaped


def normalize_markdown_lists(text: str) -> str:
    lines = []
    for line in text.splitlines():
        leading = line[: len(line) - len(line.lstrip())]
        stripped = line.strip()
        heading_match = re.fullmatch(r"#{1,6}\s+(.+)", stripped)
        bullet_match = re.fullmatch(r"[-*]\s+(.+)", stripped)
        numbered_match = re.fullmatch(r"(\d+)[.)]\s+(.+)", stripped)

        if heading_match:
            lines.append(f"{leading}**{heading_match.group(1)}**")
        elif bullet_match:
            lines.append(f"{leading}• {bullet_match.group(1)}")
        elif numbered_match:
            lines.append(f"{leading}{numbered_match.group(1)}. {numbered_match.group(2)}")
        else:
            lines.append(line)
    return "\n".join(lines)
