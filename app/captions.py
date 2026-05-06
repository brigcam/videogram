import html


VIDEO_CAPTION_LIMIT = 1024


def build_video_caption(source_url: str, title: str, description: str = "") -> str:
    title = title.strip() or "Video"
    description = normalize_caption_text(description)
    escaped_source_url = html.escape(source_url, quote=True)

    def title_markup(raw_title: str) -> str:
        return f'<a href="{escaped_source_url}">{html.escape(raw_title)}</a>'

    full_title_caption = title_markup(title)
    if len(full_title_caption) > VIDEO_CAPTION_LIMIT:
        title = trim_text_for_html_template(title, title_markup, VIDEO_CAPTION_LIMIT)
        return title_markup(title)

    if not description:
        return full_title_caption

    def caption_with_description(raw_description: str) -> str:
        return f"{full_title_caption}\n\n<blockquote>{html.escape(raw_description)}</blockquote>"

    full_caption = caption_with_description(description)
    if len(full_caption) <= VIDEO_CAPTION_LIMIT:
        return full_caption

    description = trim_text_for_html_template(description, caption_with_description, VIDEO_CAPTION_LIMIT)
    if not description:
        return full_title_caption
    return caption_with_description(description)


def normalize_caption_text(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    collapsed_lines: list[str] = []
    previous_empty = False
    for line in lines:
        is_empty = not line
        if is_empty and previous_empty:
            continue
        collapsed_lines.append(line)
        previous_empty = is_empty
    return "\n".join(collapsed_lines).strip()


def trim_text_for_html_template(text: str, template, max_length: int) -> str:
    suffix = "..."
    if len(template(suffix)) > max_length:
        return ""

    low = 0
    high = len(text)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = text[:middle].rstrip()
        if candidate and middle < len(text):
            candidate = f"{candidate}{suffix}"
        if len(template(candidate)) <= max_length:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best
