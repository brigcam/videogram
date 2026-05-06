import logging
import tempfile
import time
import uuid
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.captions import build_video_caption
from app.config import load_settings
from app.downloader import DownloadError, TranscriptError, VideoDownloader
from app.errors import classify_download_error, classify_transcript_error, classify_upload_error
from app.links import extract_supported_links
from app.logging_config import configure_logging
from app.summarizer import OpenAISummarizer, SummaryError
from app.telegram_formatting import summary_markdown_to_telegram_html


logger = logging.getLogger(__name__)


def group_chat_is_allowed(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    allowed_chat_ids: frozenset[int] = context.application.bot_data.get("allowed_chat_ids", frozenset())
    return not allowed_chat_ids or chat_id in allowed_chat_ids


def private_user_is_allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    allowed_user_ids: frozenset[int] = context.application.bot_data.get("allowed_user_ids", frozenset())
    return not allowed_user_ids or user_id in allowed_user_ids


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
            "Ciao, sono Videogram. Mandami un link video supportato e lo ripubblico come video nativo Telegram."
        )


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
    downloader: VideoDownloader = context.application.bot_data["downloader"]
    for link in links[:3]:
        request_id = uuid.uuid4().hex[:12]
        request_started_at = time.perf_counter()
        status = await message.reply_text("Preparo il video...")
        downloaded = None
        try:
            logger.info(
                "request_id=%s process_start chat_id=%s message_id=%s url=%s",
                request_id,
                message.chat_id,
                message.message_id,
                link,
            )
            await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_VIDEO)
            downloaded = await downloader.download(link, request_id)

            caption = build_video_caption(downloaded.source_url, downloaded.title, downloaded.description)
            upload_started_at = time.perf_counter()
            logger.info(
                "request_id=%s upload_start cached=%s path=%s size_bytes=%s",
                request_id,
                downloaded.cached,
                downloaded.path,
                downloaded.path.stat().st_size,
            )
            sent_message = await reply_video_with_cache(message, downloader, downloaded, caption, request_id)
            save_telegram_video_file_id(downloader, downloaded, sent_message, request_id)
            logger.info(
                "request_id=%s upload_complete upload_elapsed_ms=%s total_elapsed_ms=%s",
                request_id,
                int((time.perf_counter() - upload_started_at) * 1000),
                int((time.perf_counter() - request_started_at) * 1000),
            )
            await maybe_send_summary(message, context, downloader, downloaded, link, request_id, status)
            await status.delete()
        except DownloadError as exc:
            logger.warning("request_id=%s process_download_failed url=%s error=%s", request_id, link, exc)
            user_error = classify_download_error(exc)
            await status.edit_text(user_error.format(request_id))
        except TelegramError as exc:
            logger.warning("request_id=%s process_upload_failed url=%s error=%s", request_id, link, exc)
            user_error = classify_upload_error(exc)
            await status.edit_text(user_error.format(request_id))
        except Exception as exc:
            logger.exception("request_id=%s process_unexpected_failed url=%s", request_id, link)
            user_error = classify_upload_error(exc)
            await status.edit_text(user_error.format(request_id))
        finally:
            if downloaded and downloaded.delete_after_send:
                downloader.remove(downloaded.path)
                logger.info("request_id=%s removed_after_send path=%s", request_id, downloaded.path)


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
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
            )
        except TelegramError as exc:
            logger.warning("request_id=%s upload_file_id_failed error=%s", request_id, exc)
            downloader.forget_telegram_file_id(downloaded)

    logger.info("request_id=%s upload_file_start path=%s", request_id, downloaded.path)
    with downloaded.path.open("rb") as video:
        return await message.reply_video(
            video=video,
            caption=caption,
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
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




async def maybe_send_summary(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    downloader: VideoDownloader,
    downloaded,
    link: str,
    request_id: str,
    status,
) -> None:
    summarizer: OpenAISummarizer | None = context.application.bot_data.get("summarizer")
    if not summarizer or not summarizer.enabled:
        return

    transcript_langs: tuple[str, ...] = context.application.bot_data.get("summary_transcript_langs", ("it", "en"))
    await status.edit_text("Cerco una trascrizione da riassumere...")
    try:
        transcript = await downloader.extract_transcript(link, request_id, transcript_langs)
    except TranscriptError as exc:
        logger.warning("request_id=%s transcript_failed url=%s error=%s", request_id, link, exc)
        user_error = classify_transcript_error(exc)
        await message.reply_text(user_error.format(request_id))
        return
    if not transcript:
        logger.info("request_id=%s summary_skipped_no_transcript url=%s", request_id, link)
        return

    await status.edit_text("Riassumo la trascrizione...")
    try:
        summary = await summarizer.summarize(
            downloaded.title,
            downloaded.source_url,
            transcript,
            downloader.cache_dir_for_url(link),
            request_id,
        )
    except SummaryError as exc:
        logger.warning("request_id=%s summary_failed url=%s error=%s", request_id, link, exc)
        await message.reply_text(f"Video inviato, ma il riassunto non e riuscito.\n\nID errore: {request_id}")
        return

    if not summary:
        return

    await message.reply_text(
        summary_markdown_to_telegram_html(summary.text)[:4096],
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    await send_transcript_file(
        message,
        downloader,
        downloader.cache_dir_for_url(link),
        transcript_langs,
        transcript.text,
        request_id,
    )


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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(handle_error)

    logger.info(
        "Videogram started download_dir=%s max_download_mb=%s max_telegram_upload_mb=%s "
        "min_free_disk_percent=%s "
        "telegram_api_base_url_configured=%s telegram_local_mode=%s "
        "log_file=%s log_max_mb=%s log_backup_count=%s ytdlp_cookies_configured=%s chat_whitelist_enabled=%s "
        "allowed_chat_count=%s user_whitelist_enabled=%s allowed_user_count=%s summaries_enabled=%s "
        "summary_model=%s summary_langs=%s",
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
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
