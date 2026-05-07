import asyncio
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from telegram import InputMediaPhoto, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.captions import build_video_caption
from app.config import load_settings
from app.downloader import DownloadedPost, DownloadError, TranscriptError, VideoDownloader
from app.errors import classify_download_error, classify_transcript_error, classify_upload_error
from app.failures import FailureRecorder
from app.links import extract_supported_links
from app.logging_config import configure_logging
from app.summarizer import OpenAISummarizer, SummaryError, SummaryResult
from app.telegram_formatting import summary_markdown_to_telegram_html
from app.transcripts import Transcript
from app.usage import UsageMonitor, format_usage_report, usage_alert_loop


logger = logging.getLogger(__name__)


COOKIE_SITE_FILENAMES = {
    "youtube": "youtube.txt",
    "reddit": "reddit.txt",
    "instagram": "instagram.txt",
    "facebook": "facebook.txt",
    "threads": "threads.txt",
    "x": "x.txt",
    "twitter": "x.txt",
    "tiktok": "tiktok.txt",
}
MAX_COOKIE_TEXT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SummaryPipelineResult:
    transcript_langs: tuple[str, ...]
    transcript: Transcript | None = None
    summary: SummaryResult | None = None
    transcript_error: TranscriptError | None = None
    summary_error: SummaryError | None = None


class SiteLimiter:
    def __init__(self, max_concurrent_per_site: int) -> None:
        self.max_concurrent_per_site = max(1, max_concurrent_per_site)
        self._queues: dict[str, asyncio.Semaphore] = {}

    def site_for_url(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "instagram.com" in host:
            return "instagram"
        if "facebook.com" in host or "fb.watch" in host:
            return "facebook"
        if "threads." in host:
            return "threads"
        if "reddit.com" in host or "redd.it" in host:
            return "reddit"
        if "tiktok.com" in host:
            return "tiktok"
        if host == "x.com" or host.endswith(".x.com") or "twitter.com" in host:
            return "x"
        return "other"

    def queue_for_site(self, site: str) -> asyncio.Semaphore:
        if site not in self._queues:
            self._queues[site] = asyncio.Semaphore(self.max_concurrent_per_site)
        return self._queues[site]


def group_chat_is_allowed(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    allowed_chat_ids: frozenset[int] = context.application.bot_data.get("allowed_chat_ids", frozenset())
    return not allowed_chat_ids or chat_id in allowed_chat_ids


def private_user_is_allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    allowed_user_ids: frozenset[int] = context.application.bot_data.get("allowed_user_ids", frozenset())
    return not allowed_user_ids or user_id in allowed_user_ids


def usage_user_is_allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    allowed_user_ids: frozenset[int] = context.application.bot_data.get("usage_allowed_user_ids", frozenset())
    return bool(user_id and user_id in allowed_user_ids)


def cookie_user_is_allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    allowed_user_ids: frozenset[int] = context.application.bot_data.get("cookie_allowed_user_ids", frozenset())
    if not allowed_user_ids:
        allowed_user_ids = context.application.bot_data.get("usage_allowed_user_ids", frozenset())
    return bool(user_id and user_id in allowed_user_ids)


def access_is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if chat.type == "private":
        user_id = update.effective_user.id if update.effective_user else None
        return private_user_is_allowed(context, user_id)
    return group_chat_is_allowed(context, chat.id)


async def ensure_access_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if access_is_allowed(update, context):
        return True

    message = update.effective_message
    user_id = update.effective_user.id if update.effective_user else None
    reason = "private_user" if chat.type == "private" else "group_chat"
    logger.warning(
        "unauthorized_access_rejected reason=%s chat_id=%s chat_type=%s user_id=%s message_id=%s",
        reason,
        chat.id,
        chat.type,
        user_id,
        message.message_id if message else None,
    )

    if message:
        try:
            if chat.type == "private":
                await message.reply_text("Non sei autorizzato a usare Videogram in privato.")
            else:
                await message.reply_text("Questa chat non e' autorizzata a usare Videogram.")
        except TelegramError as exc:
            logger.warning("unauthorized_access_reply_failed chat_id=%s error=%s", chat.id, exc)

    if chat.type != "private":
        try:
            await context.bot.leave_chat(chat.id)
            logger.warning("unauthorized_chat_left chat_id=%s chat_type=%s", chat.id, chat.type)
        except TelegramError as exc:
            logger.warning("unauthorized_chat_leave_failed chat_id=%s error=%s", chat.id, exc)
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        if not await ensure_access_allowed(update, context):
            return
        logger.info(
            "start_command chat_id=%s user_id=%s message_id=%s",
            update.effective_chat.id if update.effective_chat else None,
            update.effective_user.id if update.effective_user else None,
            update.effective_message.message_id,
        )
        await update.effective_message.reply_text(
            "Ciao, sono Videogram. Mandami un link supportato e lo ripubblico come contenuto nativo Telegram."
        )


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user_id = update.effective_user.id if update.effective_user else None
    if not message or not chat:
        return
    if chat.type != "private" or not usage_user_is_allowed(context, user_id):
        logger.warning(
            "usage_command_rejected chat_id=%s chat_type=%s user_id=%s",
            chat.id,
            chat.type,
            user_id,
        )
        if chat.type == "private":
            await message.reply_text("Non sei autorizzato a leggere le statistiche di utilizzo.")
        return

    monitor: UsageMonitor | None = context.application.bot_data.get("usage_monitor")
    if not monitor:
        await message.reply_text("Monitor utilizzo non configurato.")
        return

    status = await message.reply_text("Leggo le statistiche di utilizzo...")
    snapshot = await monitor.snapshot()
    await status.edit_text(format_usage_report(snapshot))


async def cookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user_id = update.effective_user.id if update.effective_user else None
    if not message or not chat:
        return
    if chat.type != "private" or not cookie_user_is_allowed(context, user_id):
        logger.warning("cookie_command_rejected chat_id=%s chat_type=%s user_id=%s", chat.id, chat.type, user_id)
        if chat.type == "private":
            await message.reply_text("Non sei autorizzato ad aggiornare i cookie.")
        return

    command_text = message.text or message.caption or ""
    try:
        site, inline_cookie_text = parse_cookie_command_text(command_text)
    except ValueError as exc:
        await message.reply_text(str(exc))
        return

    try:
        cookie_text = await extract_cookie_command_payload(message, context, inline_cookie_text)
        normalized_cookie_text = normalize_netscape_cookie_text(cookie_text)
        cookie_path = write_cookie_file(context, site, normalized_cookie_text)
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    except OSError as exc:
        logger.warning("cookie_update_failed site=%s error=%s", site, exc)
        await message.reply_text(
            "Non sono riuscito a salvare i cookie. Controlla che la cartella cookies sia montata in scrittura."
        )
        return
    except TelegramError as exc:
        logger.warning("cookie_download_failed site=%s error=%s", site, exc)
        await message.reply_text("Non sono riuscito a leggere il file cookie da Telegram.")
        return

    logger.info(
        "cookie_update_complete site=%s path=%s bytes=%s",
        site,
        cookie_path,
        len(normalized_cookie_text.encode("utf-8")),
    )
    await message.reply_text(f"Cookie aggiornati per {site}. Le prossime richieste useranno il nuovo file.")


def parse_cookie_command_text(text: str) -> tuple[str, str]:
    parts = (text or "").strip().split(maxsplit=2)
    if not parts:
        raise ValueError(cookie_command_usage())
    command = parts[0].split("@", 1)[0].lower()
    if command != "/cookie":
        raise ValueError(cookie_command_usage())
    if len(parts) < 2:
        raise ValueError(cookie_command_usage())
    site = parts[1].strip().lower()
    if site not in COOKIE_SITE_FILENAMES:
        raise ValueError(
            "Sito non supportato. Usa uno tra: " + ", ".join(sorted(COOKIE_SITE_FILENAMES))
        )
    return site, parts[2] if len(parts) >= 3 else ""


def cookie_command_usage() -> str:
    return (
        "Uso: /cookie sito contenuto_cookie\n"
        "Oppure invia /cookie sito in reply a un file .txt o a un messaggio con cookie Netscape.\n"
        "Esempio: /cookie instagram"
    )


async def extract_cookie_command_payload(message, context: ContextTypes.DEFAULT_TYPE, inline_cookie_text: str) -> str:
    if inline_cookie_text.strip():
        return inline_cookie_text

    target_message = message.reply_to_message or message
    document = target_message.document
    if document:
        if document.file_size and document.file_size > MAX_COOKIE_TEXT_BYTES:
            raise ValueError("Il file cookie e' troppo grande. Limite massimo: 1 MB.")
        telegram_file = await document.get_file()
        cookie_bytes = bytes(await telegram_file.download_as_bytearray())
        if len(cookie_bytes) > MAX_COOKIE_TEXT_BYTES:
            raise ValueError("Il file cookie e' troppo grande. Limite massimo: 1 MB.")
        try:
            return cookie_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Il file cookie deve essere testo UTF-8 in formato Netscape.") from exc

    if target_message is not message:
        text = target_message.text or target_message.caption or ""
        if text.strip():
            return text

    raise ValueError(cookie_command_usage())


def normalize_netscape_cookie_text(cookie_text: str) -> str:
    if "\x00" in cookie_text:
        raise ValueError("Cookie non validi: il testo contiene byte null.")

    normalized = cookie_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Cookie vuoti.")
    if len(normalized.encode("utf-8")) > MAX_COOKIE_TEXT_BYTES:
        raise ValueError("Cookie troppo grandi. Limite massimo: 1 MB.")

    lines = normalized.split("\n")
    has_cookie_row = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split("\t")
        if len(fields) < 7:
            fields = re.split(r"\s+", stripped, maxsplit=6)
        if len(fields) < 7:
            raise ValueError("Cookie non validi: formato Netscape atteso con 7 colonne.")
        has_cookie_row = True

    if not has_cookie_row:
        raise ValueError("Cookie non validi: non ho trovato righe cookie.")

    if not lines[0].startswith("# Netscape HTTP Cookie File"):
        normalized = "# Netscape HTTP Cookie File\n" + normalized
    return normalized.rstrip() + "\n"


def write_cookie_file(context: ContextTypes.DEFAULT_TYPE, site: str, cookie_text: str) -> Path:
    cookies_dir_raw = context.application.bot_data.get("ytdlp_cookies_dir", "")
    if not cookies_dir_raw:
        raise ValueError("YTDLP_COOKIES_DIR non e' configurato.")
    cookies_dir = Path(cookies_dir_raw)
    filename = COOKIE_SITE_FILENAMES[site]
    cookies_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = cookies_dir / filename
    temp_path = cookies_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    temp_path.write_text(cookie_text, encoding="utf-8")
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, cookie_path)
    return cookie_path


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    if not await ensure_access_allowed(update, context):
        return

    text = message.text or message.caption or ""
    links = extract_supported_links(text)
    if not links:
        return

    logger.info(
        "message_links_detected chat_id=%s user_id=%s message_id=%s link_count=%s",
        message.chat_id,
        update.effective_user.id if update.effective_user else None,
        message.message_id,
        len(links),
    )
    for link in links[:3]:
        await process_link(message, context, link)


async def process_link(message, context: ContextTypes.DEFAULT_TYPE, link: str) -> None:
    downloader: VideoDownloader = context.application.bot_data["downloader"]
    queue: asyncio.Semaphore = context.application.bot_data["processing_queue"]
    site_limiter: SiteLimiter = context.application.bot_data["site_limiter"]
    site = site_limiter.site_for_url(link)
    site_queue = site_limiter.queue_for_site(site)
    request_id = uuid.uuid4().hex[:12]
    request_started_at = time.perf_counter()
    status = await message.reply_text("Preparo il contenuto...")
    site_queued = site_queue.locked()
    global_queued = queue.locked()
    if site_queued:
        await safe_status_edit(status, f"Richiesta in coda per {site}...", request_id, "site_queue_status")
        logger.info("request_id=%s process_site_queued site=%s url=%s", request_id, site, link)
    elif global_queued:
        await safe_status_edit(status, "Richiesta in coda generale...", request_id, "queue_status")
        logger.info("request_id=%s process_queued site=%s url=%s", request_id, site, link)

    async with site_queue:
        if site_queued and queue.locked():
            await safe_status_edit(status, "Turno del sito arrivato, attendo uno slot globale...", request_id, "global_queue_status")
        async with queue:
            if site_queued or global_queued:
                await safe_status_edit(status, "Preparo il contenuto...", request_id, "queue_start_status")
            await run_link_job(message, context, downloader, link, request_id, request_started_at, status)


async def run_link_job(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    downloader: VideoDownloader,
    link: str,
    request_id: str,
    request_started_at: float,
    status,
) -> None:
    summary_task = start_summary_task(context, downloader, link, request_id)
    post = None
    try:
        logger.info(
            "request_id=%s process_start chat_id=%s message_id=%s url=%s",
            request_id,
            message.chat_id,
            message.message_id,
            link,
        )
        await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_VIDEO)
        post = await downloader.download_post(link, request_id)

        caption = build_video_caption(post.source_url, post.title, post.description)
        upload_started_at = time.perf_counter()
        await safe_status_edit(status, "Contenuto scaricato, carico su Telegram...", request_id, "upload_status")
        await send_downloaded_post(message, downloader, post, caption, request_id)
        logger.info(
            "request_id=%s upload_complete upload_elapsed_ms=%s total_elapsed_ms=%s",
            request_id,
            int((time.perf_counter() - upload_started_at) * 1000),
            int((time.perf_counter() - request_started_at) * 1000),
        )
        await wait_for_summary_status(status, summary_task)
        await publish_summary_task(message, context, downloader, link, request_id, summary_task, post)
        await safe_status_delete(status, request_id)
    except asyncio.CancelledError:
        logger.warning("request_id=%s process_cancelled url=%s", request_id, link)
        record_failed_link(
            context,
            request_id,
            link,
            "cancelled",
            RuntimeError("Processing was interrupted while the bot was stopping."),
            message,
        )
        await safe_status_edit(
            status,
            f"Operazione interrotta dal riavvio del bot. Riprova il link tra poco.\n\nID richiesta: {request_id}",
            request_id,
            "cancelled_status",
        )
        raise
    except DownloadError as exc:
        logger.warning("request_id=%s process_download_failed url=%s error=%s", request_id, link, exc)
        record_failed_link(context, request_id, link, "download", exc, message)
        user_error = classify_download_error(exc)
        await safe_status_edit(status, user_error.format(request_id), request_id, "download_error_status")
        await publish_summary_task(message, context, downloader, link, request_id, summary_task, post)
    except TelegramError as exc:
        logger.warning("request_id=%s process_upload_failed url=%s error=%s", request_id, link, exc)
        record_failed_link(context, request_id, link, "upload", exc, message)
        user_error = classify_upload_error(exc)
        await safe_status_edit(status, user_error.format(request_id), request_id, "upload_error_status")
        await publish_summary_task(message, context, downloader, link, request_id, summary_task, post)
    except Exception as exc:
        logger.exception("request_id=%s process_unexpected_failed url=%s", request_id, link)
        record_failed_link(context, request_id, link, "unexpected", exc, message)
        user_error = classify_upload_error(exc)
        await safe_status_edit(status, user_error.format(request_id), request_id, "unexpected_error_status")
        await publish_summary_task(message, context, downloader, link, request_id, summary_task, post)
    finally:
        if post and post.delete_after_send:
            downloader.remove(post.cache_dir)
            logger.info("request_id=%s removed_after_send path=%s", request_id, post.cache_dir)


def record_failed_link(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: str,
    link: str,
    stage: str,
    error: Exception,
    message,
) -> None:
    recorder: FailureRecorder | None = context.application.bot_data.get("failure_recorder")
    if not recorder:
        return
    chat_type = message.chat.type if getattr(message, "chat", None) else ""
    recorder.record(request_id=request_id, url=link, stage=stage, error=error, chat_type=chat_type)


async def safe_status_edit(status, text: str, request_id: str, action: str) -> None:
    try:
        await status.edit_text(text)
    except TelegramError as exc:
        logger.warning("request_id=%s %s_failed error=%s", request_id, action, exc)


async def safe_status_delete(status, request_id: str) -> None:
    try:
        await status.delete()
    except TelegramError as exc:
        logger.warning("request_id=%s status_delete_failed error=%s", request_id, exc)


async def send_downloaded_post(
    message,
    downloader: VideoDownloader,
    post: DownloadedPost,
    caption: str,
    request_id: str,
) -> None:
    if post.video:
        logger.info(
            "request_id=%s upload_start type=video cached=%s path=%s size_bytes=%s",
            request_id,
            post.video.cached,
            post.video.path,
            post.video.path.stat().st_size,
        )
        sent_message = await reply_video_with_cache(message, downloader, post.video, caption, request_id)
        save_telegram_video_file_id(downloader, post.video, sent_message, request_id)
        return

    if post.photos:
        logger.info(
            "request_id=%s upload_start type=photo photo_count=%s cached=%s",
            request_id,
            len(post.photos),
            post.cached,
        )
        sent_messages = await reply_photos_with_cache(message, downloader, post, caption, request_id)
        save_telegram_photo_file_ids(downloader, post, sent_messages, request_id)
        if post.audio:
            logger.info(
                "request_id=%s upload_start type=photo_audio cached=%s path=%s size_bytes=%s",
                request_id,
                post.audio.cached,
                post.audio.path,
                post.audio.path.stat().st_size,
            )
            audio_caption = build_video_caption(post.source_url, post.audio.title)
            sent_audio = await reply_audio_with_cache(message, downloader, post.audio, audio_caption, request_id)
            save_telegram_audio_file_id(downloader, post.audio, sent_audio, request_id)
        return

    if post.audio:
        logger.info(
            "request_id=%s upload_start type=audio cached=%s path=%s size_bytes=%s",
            request_id,
            post.audio.cached,
            post.audio.path,
            post.audio.path.stat().st_size,
        )
        sent_message = await reply_audio_with_cache(message, downloader, post.audio, caption, request_id)
        save_telegram_audio_file_id(downloader, post.audio, sent_message, request_id)
        return

    text = build_video_caption(post.source_url, post.title, post.text or post.description)
    logger.info("request_id=%s upload_start type=text chars=%s cached=%s", request_id, len(text), post.cached)
    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def reply_photos_with_cache(message, downloader: VideoDownloader, post: DownloadedPost, caption: str, request_id: str):
    if post.photos and all(photo.telegram_file_id for photo in post.photos):
        try:
            logger.info(
                "request_id=%s upload_photo_file_id_start photo_count=%s",
                request_id,
                len(post.photos),
            )
            media = [
                InputMediaPhoto(
                    media=photo.telegram_file_id,
                    caption=caption if index == 0 else None,
                    parse_mode=ParseMode.HTML if index == 0 else None,
                )
                for index, photo in enumerate(post.photos[:10])
            ]
            return await message.reply_media_group(media=media, read_timeout=120, write_timeout=120)
        except TelegramError as exc:
            logger.warning("request_id=%s upload_photo_file_id_failed error=%s", request_id, exc)
            downloader.forget_post_photo_file_ids(post)

    logger.info("request_id=%s upload_photo_file_start photo_count=%s", request_id, len(post.photos))
    if len(post.photos) == 1:
        with post.photos[0].path.open("rb") as photo:
            return [
                await message.reply_photo(
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                    pool_timeout=30,
                )
            ]

    files = [photo.path.open("rb") for photo in post.photos[:10]]
    try:
        media = [
            InputMediaPhoto(
                media=file,
                caption=caption if index == 0 else None,
                parse_mode=ParseMode.HTML if index == 0 else None,
            )
            for index, file in enumerate(files)
        ]
        return await message.reply_media_group(media=media, read_timeout=120, write_timeout=120)
    finally:
        for file in files:
            file.close()


def save_telegram_photo_file_ids(
    downloader: VideoDownloader,
    post: DownloadedPost,
    sent_messages,
    request_id: str,
) -> None:
    file_ids: list[str] = []
    for sent_message in sent_messages or []:
        if not sent_message.photo:
            continue
        file_ids.append(sent_message.photo[-1].file_id)
    if not file_ids:
        return
    downloader.save_post_photo_file_ids(post, file_ids)
    logger.info(
        "request_id=%s telegram_photo_file_ids_saved cache_dir=%s count=%s",
        request_id,
        post.cache_dir,
        len(file_ids),
    )


async def reply_audio_with_cache(message, downloader: VideoDownloader, audio, caption: str, request_id: str):
    if audio.telegram_file_id:
        try:
            logger.info("request_id=%s upload_audio_file_id_start cached=%s", request_id, audio.cached)
            return await message.reply_audio(
                audio=audio.telegram_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                title=audio.title,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )
        except TelegramError as exc:
            logger.warning("request_id=%s upload_audio_file_id_failed error=%s", request_id, exc)
            downloader.forget_audio_file_id(audio)

    logger.info("request_id=%s upload_audio_file_start path=%s", request_id, audio.path)
    with audio.path.open("rb") as audio_file:
        return await message.reply_audio(
            audio=audio_file,
            caption=caption,
            parse_mode=ParseMode.HTML,
            title=audio.title,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
            pool_timeout=30,
        )


def save_telegram_audio_file_id(downloader: VideoDownloader, audio, sent_message, request_id: str) -> None:
    if not sent_message or not sent_message.audio:
        return
    file_id = sent_message.audio.file_id
    if not file_id:
        return
    downloader.save_audio_file_id(audio, file_id)
    logger.info("request_id=%s telegram_audio_file_id_saved cache_dir=%s", request_id, audio.cache_dir)


async def reply_video_with_cache(message, downloader: VideoDownloader, downloaded, caption: str, request_id: str):
    if downloaded.telegram_file_id:
        try:
            logger.info(
                "request_id=%s upload_file_id_start cached=%s file_id_present=True",
                request_id,
                downloaded.cached,
            )
            return await message.reply_video(
                video=downloaded.telegram_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
                width=downloaded.width or None,
                height=downloaded.height or None,
                duration=downloaded.duration or None,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )
        except TelegramError as exc:
            logger.warning("request_id=%s upload_file_id_failed error=%s", request_id, exc)
            downloader.forget_telegram_file_id(downloaded)

    thumbnail_path = downloader.ensure_video_thumbnail(downloaded, request_id)
    logger.info(
        "request_id=%s upload_file_start path=%s thumbnail=%s width=%s height=%s duration=%s",
        request_id,
        downloaded.path,
        bool(thumbnail_path),
        downloaded.width,
        downloaded.height,
        downloaded.duration,
    )
    with downloaded.path.open("rb") as video:
        if thumbnail_path:
            with thumbnail_path.open("rb") as thumbnail:
                return await message.reply_video(
                    video=video,
                    thumbnail=thumbnail,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                    width=downloaded.width or None,
                    height=downloaded.height or None,
                    duration=downloaded.duration or None,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                    pool_timeout=30,
                )
        return await message.reply_video(
            video=video,
            caption=caption,
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
            width=downloaded.width or None,
            height=downloaded.height or None,
            duration=downloaded.duration or None,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
            pool_timeout=30,
        )


def save_telegram_video_file_id(downloader: VideoDownloader, downloaded, sent_message, request_id: str) -> None:
    if not sent_message or not sent_message.video:
        return
    file_id = sent_message.video.file_id
    if not file_id:
        return
    downloader.save_telegram_file_id(downloaded, file_id)
    logger.info("request_id=%s telegram_file_id_saved cache_dir=%s", request_id, downloaded.cache_dir)




def start_summary_task(
    context: ContextTypes.DEFAULT_TYPE,
    downloader: VideoDownloader,
    link: str,
    request_id: str,
) -> asyncio.Task[SummaryPipelineResult | None] | None:
    summarizer: OpenAISummarizer | None = context.application.bot_data.get("summarizer")
    if not summarizer or not summarizer.enabled:
        return None

    transcript_langs: tuple[str, ...] = context.application.bot_data.get("summary_transcript_langs", ("it", "en"))
    logger.info("request_id=%s summary_task_scheduled url=%s", request_id, link)
    return asyncio.create_task(
        prepare_summary(downloader, summarizer, link, request_id, transcript_langs),
        name=f"summary-{request_id}",
    )


async def prepare_summary(
    downloader: VideoDownloader,
    summarizer: OpenAISummarizer,
    link: str,
    request_id: str,
    transcript_langs: tuple[str, ...],
) -> SummaryPipelineResult:
    logger.info("request_id=%s summary_task_start url=%s", request_id, link)
    try:
        transcript = await downloader.extract_transcript(link, request_id, transcript_langs)
    except TranscriptError as exc:
        logger.warning("request_id=%s transcript_failed url=%s error=%s", request_id, link, exc)
        return SummaryPipelineResult(transcript_langs=transcript_langs, transcript_error=exc)
    if not transcript:
        logger.info("request_id=%s summary_skipped_no_transcript url=%s", request_id, link)
        return SummaryPipelineResult(transcript_langs=transcript_langs)

    try:
        summary = await summarizer.summarize(
            "Contenuto",
            link,
            transcript,
            downloader.cache_dir_for_url(link),
            request_id,
        )
    except SummaryError as exc:
        logger.warning("request_id=%s summary_failed url=%s error=%s", request_id, link, exc)
        return SummaryPipelineResult(transcript_langs=transcript_langs, transcript=transcript, summary_error=exc)

    logger.info(
        "request_id=%s summary_task_complete url=%s transcript_chars=%s summary_cached=%s",
        request_id,
        link,
        len(transcript.text),
        summary.cached if summary else None,
    )
    return SummaryPipelineResult(transcript_langs=transcript_langs, transcript=transcript, summary=summary)


async def wait_for_summary_status(status, summary_task: asyncio.Task[SummaryPipelineResult | None] | None) -> None:
    if not summary_task or summary_task.done():
        return
    try:
        await status.edit_text("Contenuto inviato, attendo il riassunto...")
    except TelegramError as exc:
        logger.warning("summary_status_update_failed error=%s", exc)


async def publish_summary_task(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    downloader: VideoDownloader,
    link: str,
    request_id: str,
    summary_task: asyncio.Task[SummaryPipelineResult | None] | None,
    post: DownloadedPost | None = None,
) -> None:
    if not summary_task:
        return

    try:
        result = await summary_task
    except Exception as exc:
        logger.exception("request_id=%s summary_task_unexpected_failed url=%s", request_id, link)
        await message.reply_text(f"Riassunto non riuscito.\n\nID errore: {request_id}")
        return

    if not result:
        return

    if result.transcript_error:
        sent_description_summary = await maybe_send_description_summary(message, context, post, link, request_id)
        if sent_description_summary:
            return
        user_error = classify_transcript_error(result.transcript_error)
        try:
            await message.reply_text(user_error.format(request_id))
        except TelegramError as exc:
            logger.warning("request_id=%s transcript_error_publish_failed url=%s error=%s", request_id, link, exc)
        return

    if result.summary_error:
        try:
            await message.reply_text(f"Trascrizione trovata, ma il riassunto non e riuscito.\n\nID errore: {request_id}")
        except TelegramError as exc:
            logger.warning("request_id=%s summary_error_publish_failed url=%s error=%s", request_id, link, exc)
        return

    if not result.transcript:
        sent_description_summary = await maybe_send_description_summary(message, context, post, link, request_id)
        if not sent_description_summary:
            try:
                await message.reply_text(f"Non ho trovato una trascrizione disponibile.\n\nID richiesta: {request_id}")
            except TelegramError as exc:
                logger.warning("request_id=%s transcript_not_found_publish_failed url=%s error=%s", request_id, link, exc)
        return

    if not result.summary:
        return

    try:
        await message.reply_text(
            summary_markdown_to_telegram_html(result.summary.text)[:4096],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        if message.chat.type == "private":
            await send_transcript_file(
                message,
                downloader,
                downloader.cache_dir_for_url(link),
                result.transcript_langs,
                result.transcript.text,
                request_id,
            )
        else:
            logger.info(
                "request_id=%s transcript_file_skipped_non_private chat_id=%s chat_type=%s",
                request_id,
                message.chat_id,
                message.chat.type,
            )
    except TelegramError as exc:
        logger.warning("request_id=%s summary_publish_failed url=%s error=%s", request_id, link, exc)


async def maybe_send_description_summary(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    post: DownloadedPost | None,
    link: str,
    request_id: str,
) -> bool:
    if not post:
        return False
    description = (post.description or post.text or "").strip()
    if not description:
        logger.info("request_id=%s description_summary_skipped_empty url=%s", request_id, link)
        return False

    summarizer: OpenAISummarizer | None = context.application.bot_data.get("summarizer")
    if not summarizer or not summarizer.enabled:
        return False

    logger.info(
        "request_id=%s description_summary_start url=%s chars=%s cache_dir=%s",
        request_id,
        link,
        len(description),
        post.cache_dir,
    )
    try:
        summary = await summarizer.summarize(
            post.title or "Post",
            post.source_url or link,
            Transcript(description, "und", "description"),
            post.cache_dir,
            request_id,
            content_kind="description",
        )
    except SummaryError as exc:
        logger.warning("request_id=%s description_summary_failed url=%s error=%s", request_id, link, exc)
        try:
            await message.reply_text(f"Non ho trovato una trascrizione, e il riassunto della descrizione non e riuscito.\n\nID errore: {request_id}")
        except TelegramError as telegram_exc:
            logger.warning("request_id=%s description_summary_error_publish_failed url=%s error=%s", request_id, link, telegram_exc)
        return True

    if not summary:
        return False

    summary_text = description_summary_message(post, summary.text)
    try:
        await message.reply_text(
            summary_text[:4096],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        logger.warning("request_id=%s description_summary_publish_failed url=%s error=%s", request_id, link, exc)
        return True

    logger.info(
        "request_id=%s description_summary_complete url=%s cached=%s chars=%s",
        request_id,
        link,
        summary.cached,
        len(summary.text),
    )
    return True


def description_summary_message(post: DownloadedPost, summary_text: str) -> str:
    summary_html = summary_markdown_to_telegram_html(summary_text)
    if not (post.video or post.audio):
        return summary_html
    note = (
        "<i>Non ho trovato una trascrizione disponibile per questo media; "
        "questo e' il riassunto del testo di accompagnamento.</i>"
    )
    return f"{note}\n\n{summary_html}"


async def send_transcript_file(
    message,
    downloader: VideoDownloader,
    cache_dir: Path,
    transcript_langs: tuple[str, ...],
    transcript_text: str,
    request_id: str,
) -> None:
    filename = "transcript.txt"
    cached_file_id = downloader.cached_transcript_file_id(cache_dir, transcript_langs)
    if cached_file_id:
        try:
            logger.info("request_id=%s transcript_file_id_send_start cache_dir=%s", request_id, cache_dir)
            sent_message = await message.reply_document(
                document=cached_file_id,
                filename=filename,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )
            logger.info("request_id=%s transcript_file_id_send_complete", request_id)
            save_transcript_document_file_id(downloader, cache_dir, transcript_langs, sent_message, request_id)
            return
        except TelegramError as exc:
            logger.warning("request_id=%s transcript_file_id_send_failed error=%s", request_id, exc)
            downloader.forget_transcript_file_id(cache_dir, transcript_langs)

    with tempfile.TemporaryDirectory() as temp_dir:
        transcript_path = Path(temp_dir) / filename
        transcript_path.write_text(transcript_text, encoding="utf-8")
        logger.info(
            "request_id=%s transcript_file_send_start path=%s chars=%s",
            request_id,
            transcript_path,
            len(transcript_text),
        )
        with transcript_path.open("rb") as transcript_file:
            sent_message = await message.reply_document(
                document=transcript_file,
                filename=filename,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )
        logger.info("request_id=%s transcript_file_send_complete", request_id)
        save_transcript_document_file_id(downloader, cache_dir, transcript_langs, sent_message, request_id)


def save_transcript_document_file_id(
    downloader: VideoDownloader,
    cache_dir: Path,
    transcript_langs: tuple[str, ...],
    sent_message,
    request_id: str,
) -> None:
    if not sent_message or not sent_message.document:
        return
    file_id = sent_message.document.file_id
    if not file_id:
        return
    downloader.save_transcript_file_id(cache_dir, transcript_langs, file_id)
    logger.info("request_id=%s transcript_document_file_id_saved cache_dir=%s", request_id, cache_dir)


async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.new_chat_members or not update.effective_chat:
        return

    bot = await context.bot.get_me()
    if not any(member.id == bot.id for member in message.new_chat_members):
        return
    if group_chat_is_allowed(context, update.effective_chat.id):
        logger.info(
            "bot_added_to_allowed_chat chat_id=%s chat_type=%s message_id=%s",
            update.effective_chat.id,
            update.effective_chat.type,
            message.message_id,
        )
        return

    logger.warning(
        "bot_added_to_unauthorized_chat chat_id=%s chat_type=%s message_id=%s",
        update.effective_chat.id,
        update.effective_chat.type,
        message.message_id,
    )
    await ensure_access_allowed(update, context)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.error:
        exc_info = (type(context.error), context.error, context.error.__traceback__)
        logger.error("telegram_update_failed update=%s", update, exc_info=exc_info)
    else:
        logger.error("telegram_update_failed update=%s", update)


async def post_init(application) -> None:
    interval_minutes: int = application.bot_data.get("usage_check_interval_minutes", 60)
    report_user_id: int = application.bot_data.get("usage_report_user_id", 0)
    task = asyncio.create_task(
        usage_alert_loop(application, interval_minutes, report_user_id),
        name="usage-alert-loop",
    )
    application.bot_data["usage_alert_task"] = task


async def post_shutdown(application) -> None:
    task: asyncio.Task | None = application.bot_data.get("usage_alert_task")
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> None:
    settings = load_settings()
    configure_logging(settings)

    downloader = VideoDownloader(
        settings.download_dir,
        settings.max_download_bytes,
        settings.max_telegram_upload_bytes,
        settings.min_free_disk_percent,
        settings.ytdlp_cookies_file,
        settings.ytdlp_cookies_dir,
    )
    summarizer = OpenAISummarizer(
        settings.openai_api_key,
        settings.openai_summary_model,
        settings.openai_summary_prompt,
        settings.openai_summary_max_transcript_chars,
    )
    application_builder = Application.builder().token(settings.telegram_bot_token)
    application_builder.post_init(post_init)
    application_builder.post_shutdown(post_shutdown)
    application_builder.concurrent_updates(settings.max_concurrent_jobs * 4)
    if settings.telegram_api_base_url:
        application_builder.base_url(settings.telegram_api_base_url)
    if settings.telegram_api_file_base_url:
        application_builder.base_file_url(settings.telegram_api_file_base_url)
    if settings.telegram_local_mode:
        application_builder.local_mode(True)
    application = application_builder.build()
    application.bot_data["downloader"] = downloader
    application.bot_data["summarizer"] = summarizer
    application.bot_data["summary_transcript_langs"] = settings.summary_transcript_langs
    application.bot_data["allowed_chat_ids"] = settings.allowed_chat_ids
    application.bot_data["allowed_user_ids"] = settings.allowed_user_ids
    application.bot_data["usage_allowed_user_ids"] = settings.usage_allowed_user_ids
    application.bot_data["cookie_allowed_user_ids"] = settings.cookie_allowed_user_ids
    application.bot_data["usage_report_user_id"] = settings.usage_report_user_id
    application.bot_data["usage_check_interval_minutes"] = settings.usage_check_interval_minutes
    application.bot_data["processing_queue"] = asyncio.Semaphore(settings.max_concurrent_jobs)
    application.bot_data["site_limiter"] = SiteLimiter(settings.site_concurrent_jobs)
    application.bot_data["ytdlp_cookies_dir"] = settings.ytdlp_cookies_dir
    application.bot_data["failure_recorder"] = FailureRecorder(Path(settings.failed_links_file))
    application.bot_data["usage_monitor"] = UsageMonitor(
        hetzner_api_token=settings.hetzner_api_token,
        hetzner_server_id=settings.hetzner_server_id,
        hetzner_monthly_traffic_tb=settings.hetzner_monthly_traffic_tb,
        openai_admin_key=settings.openai_admin_key,
        openai_monthly_budget_usd=settings.openai_monthly_budget_usd,
        alert_step_percent=settings.usage_alert_step_percent,
        alert_state_file=settings.usage_alert_state_file,
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("usage", usage_command))
    private_cookie_filter = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("cookie", cookie_command, filters=private_cookie_filter))
    application.add_handler(
        MessageHandler(
            filters.Document.ALL & filters.CaptionRegex(r"^/cookie(?:@\w+)?(?:\s|$)") & private_cookie_filter,
            cookie_command,
        )
    )
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(handle_error)

    logger.info(
        "Videogram started download_dir=%s max_download_mb=%s max_telegram_upload_mb=%s "
        "min_free_disk_percent=%s "
        "telegram_api_base_url_configured=%s telegram_local_mode=%s "
        "log_file=%s log_max_mb=%s log_backup_count=%s ytdlp_cookies_configured=%s chat_whitelist_enabled=%s "
        "allowed_chat_count=%s user_whitelist_enabled=%s allowed_user_count=%s summaries_enabled=%s "
        "summary_model=%s summary_langs=%s max_concurrent_jobs=%s site_concurrent_jobs=%s "
        "concurrent_updates=%s failed_links_file=%s "
        "usage_allowed_user_count=%s cookie_allowed_user_count=%s usage_report_enabled=%s hetzner_usage_configured=%s "
        "openai_costs_configured=%s openai_budget_configured=%s",
        settings.download_dir,
        settings.max_download_mb,
        settings.max_telegram_upload_mb,
        settings.min_free_disk_percent,
        bool(settings.telegram_api_base_url),
        settings.telegram_local_mode,
        settings.log_file,
        settings.log_max_mb,
        settings.log_backup_count,
        bool(settings.ytdlp_cookies_file or settings.ytdlp_cookies_dir),
        bool(settings.allowed_chat_ids),
        len(settings.allowed_chat_ids),
        bool(settings.allowed_user_ids),
        len(settings.allowed_user_ids),
        summarizer.enabled,
        settings.openai_summary_model,
        ",".join(settings.summary_transcript_langs),
        settings.max_concurrent_jobs,
        settings.site_concurrent_jobs,
        settings.max_concurrent_jobs * 4,
        settings.failed_links_file,
        len(settings.usage_allowed_user_ids),
        len(settings.cookie_allowed_user_ids),
        bool(settings.usage_report_user_id),
        bool(settings.hetzner_api_token and settings.hetzner_server_id),
        bool(settings.openai_admin_key),
        bool(settings.openai_monthly_budget_usd > 0),
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
