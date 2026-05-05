import html
import logging

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import load_settings
from app.downloader import DownloadError, VideoDownloader
from app.links import extract_supported_links


logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
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

    downloader: VideoDownloader = context.application.bot_data["downloader"]
    for link in links[:3]:
        status = await message.reply_text("Preparo il video...")
        downloaded = None
        try:
            await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_VIDEO)
            downloaded = await downloader.download(link)

            caption = f'<a href="{html.escape(downloaded.source_url)}">{html.escape(downloaded.title)}</a>'
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
            await status.delete()
        except DownloadError as exc:
            logger.warning("Download failed for %s: %s", link, exc)
            await status.edit_text("Non sono riuscito a scaricare questo video. Riprova con un altro link.")
        except Exception:
            logger.exception("Unexpected failure while processing %s", link)
            await status.edit_text("Qualcosa è andato storto mentre preparavo il video.")
        finally:
            if downloaded and downloaded.delete_after_send:
                downloader.remove(downloaded.path)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    downloader = VideoDownloader(
        settings.download_dir,
        settings.max_download_bytes,
        settings.min_free_disk_percent,
    )
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["downloader"] = downloader
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Videogram started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
