import html
import logging
import time
import uuid

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import load_settings
from app.downloader import DownloadError, VideoDownloader
from app.errors import classify_download_error, classify_upload_error
from app.links import extract_supported_links
from app.logging_config import configure_logging


logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
        logger.info(
            "start_command chat_id=%s user_id=%s message_id=%s",
            update.effective_chat.id if update.effective_chat else None,
            update.effective_user.id if update.effective_user else None,
            update.effective_message.message_id,
        )
        await update.effective_message.reply_text(
            "Ciao, sono Videogram. Mandami un link YouTube e lo ripubblico come video nativo Telegram."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
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

            caption = f'<a href="{html.escape(downloaded.source_url)}">{html.escape(downloaded.title)}</a>'
            upload_started_at = time.perf_counter()
            logger.info(
                "request_id=%s upload_start cached=%s path=%s size_bytes=%s",
                request_id,
                downloaded.cached,
                downloaded.path,
                downloaded.path.stat().st_size,
            )
            with downloaded.path.open("rb") as video:
                await message.reply_video(
                    video=video,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                    pool_timeout=30,
                )
            logger.info(
                "request_id=%s upload_complete upload_elapsed_ms=%s total_elapsed_ms=%s",
                request_id,
                int((time.perf_counter() - upload_started_at) * 1000),
                int((time.perf_counter() - request_started_at) * 1000),
            )
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
        settings.min_free_disk_percent,
        settings.ytdlp_cookies_file,
    )
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["downloader"] = downloader
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(handle_error)

    logger.info(
        "Videogram started download_dir=%s max_download_mb=%s min_free_disk_percent=%s "
        "log_file=%s log_max_mb=%s log_backup_count=%s ytdlp_cookies_configured=%s",
        settings.download_dir,
        settings.max_download_mb,
        settings.min_free_disk_percent,
        settings.log_file,
        settings.log_max_mb,
        settings.log_backup_count,
        bool(settings.ytdlp_cookies_file),
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
