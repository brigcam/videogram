import re
from dataclasses import dataclass


@dataclass(frozen=True)
class UserErrorMessage:
    title: str
    detail: str

    def format(self, request_id: str) -> str:
        return f"{self.title}\n{self.detail}\n\nID errore: {request_id}"


def classify_download_error(error: Exception) -> UserErrorMessage:
    message = str(error).lower()

    if "sign in to confirm" in message or "not a bot" in message:
        return UserErrorMessage(
            "YouTube ha richiesto una verifica anti-bot.",
            "Ho provato a scaricare il video, ma YouTube chiede una sessione valida. "
            "Controlla che i cookies siano presenti e aggiornati.",
        )

    if "account authentication is required" in message and "reddit" in message:
        return UserErrorMessage(
            "Reddit richiede una sessione autenticata.",
            "Esporta i cookies Reddit in ./cookies/reddit.txt e riprova.",
        )

    if ("login required" in message or "authentication" in message or "cookies" in message) and "instagram" in message:
        return UserErrorMessage(
            "Instagram richiede una sessione autenticata.",
            "Esporta i cookies Instagram in ./cookies/instagram.txt e riprova.",
        )

    if ("login required" in message or "authentication" in message or "cookies" in message) and "facebook" in message:
        return UserErrorMessage(
            "Facebook richiede una sessione autenticata.",
            "Esporta i cookies Facebook in ./cookies/facebook.txt e riprova.",
        )

    if ("login required" in message or "authentication" in message or "cookies" in message) and (
        "twitter" in message or "x.com" in message
    ):
        return UserErrorMessage(
            "X/Twitter richiede una sessione autenticata.",
            "Esporta i cookies di X in ./cookies/x.txt e riprova.",
        )

    if ("login required" in message or "authentication" in message or "cookies" in message) and "tiktok" in message:
        return UserErrorMessage(
            "TikTok richiede una sessione autenticata.",
            "Esporta i cookies TikTok in ./cookies/tiktok.txt e riprova.",
        )

    if "unsupported url" in message and ("threads.com" in message or "threads.net" in message):
        return UserErrorMessage(
            "Threads non e stato scaricato.",
            "Il link e stato riconosciuto, ma l'estrattore Threads non e riuscito a gestirlo. "
            "Controlla che il plugin yt-dlp Threads sia installato e aggiornato.",
        )

    if "read-only file system" in message and "cookies" in message:
        return UserErrorMessage(
            "Il file cookies non e scrivibile dal container.",
            "yt-dlp ha bisogno di poter aggiornare i cookies. Controlla il mount della cartella cookies.",
        )

    if "private video" in message or "video is private" in message:
        return UserErrorMessage(
            "Questo video e privato.",
            "Non posso scaricarlo senza un account che abbia accesso al contenuto.",
        )

    if "age" in message and ("restricted" in message or "confirm" in message):
        return UserErrorMessage(
            "Questo video sembra avere restrizioni di eta.",
            "Serve una sessione YouTube valida nei cookies per poterlo scaricare.",
        )

    if "video unavailable" in message or "this video is unavailable" in message:
        return UserErrorMessage(
            "Questo video non risulta disponibile.",
            "Potrebbe essere stato rimosso, bloccato per area geografica o non accessibile dal server.",
        )

    if "larger than the configured limit" in message or "file is larger than max-filesize" in message:
        size_detail = format_size_limit_detail(str(error))
        detail = "Aumenta MAX_DOWNLOAD_MB oppure prova un video piu piccolo."
        if size_detail:
            detail = f"{size_detail}\n{detail}"
        return UserErrorMessage(
            "Il video supera il limite configurato.",
            detail,
        )

    if "larger than the telegram upload limit" in message:
        size_detail = format_size_limit_detail(str(error))
        detail = (
            "Il Bot API pubblico accetta upload fino a circa 50 MB. "
            "Riduci MAX_TELEGRAM_UPLOAD_MB solo per tenere piu margine, oppure prova un video piu piccolo."
        )
        if size_detail:
            detail = f"{size_detail}\n{detail}"
        return UserErrorMessage(
            "Il video supera il limite upload di Telegram.",
            detail,
        )

    if "requested format is not available" in message or "no video formats found" in message:
        return UserErrorMessage(
            "Non ho trovato un formato video scaricabile.",
            "YouTube potrebbe aver richiesto una challenge JavaScript/EJS oppure non sta offrendo un formato compatibile.",
        )

    if "unsupported url" in message:
        return UserErrorMessage(
            "Questo link non e supportato.",
            "Videogram gestisce solo link riconosciuti delle piattaforme supportate.",
        )

    if "timed out" in message or "timeout" in message:
        return UserErrorMessage(
            "Il download e andato in timeout.",
            "Il server o YouTube hanno risposto troppo lentamente. Riprova tra poco.",
        )

    return UserErrorMessage(
        "Non sono riuscito a scaricare questo video.",
        "Il downloader ha restituito un errore non classificato. Controlla i log con l'ID errore qui sotto.",
    )


def classify_upload_error(error: Exception) -> UserErrorMessage:
    message = str(error).lower()

    if "file is too big" in message or "request entity too large" in message:
        return UserErrorMessage(
            "Telegram ha rifiutato il file per dimensione.",
            "Il download e riuscito, ma l'upload del video e troppo grande per questa modalita bot.",
        )

    if "timed out" in message or "timeout" in message:
        return UserErrorMessage(
            "Upload Telegram in timeout.",
            "Il download e riuscito, ma Telegram non ha completato l'upload in tempo.",
        )

    if "forbidden" in message or "bot was blocked" in message:
        return UserErrorMessage(
            "Telegram ha bloccato l'invio in questa chat.",
            "Controlla che il bot abbia accesso alla chat e i permessi necessari.",
        )

    return UserErrorMessage(
        "Qualcosa e andato storto durante l'invio del video.",
        "Il download potrebbe essere riuscito, ma Telegram ha restituito un errore. Controlla i log con l'ID qui sotto.",
    )


def format_size_limit_detail(raw_message: str) -> str:
    parsed = parse_size_limit(raw_message)
    if not parsed:
        return ""
    size_bytes, max_bytes = parsed
    return f"Dimensione video: {format_bytes(size_bytes)}. Limite configurato: {format_bytes(max_bytes)}."


def parse_size_limit(raw_message: str) -> tuple[int, int] | None:
    lower_message = raw_message.lower()
    key_value_match = re.search(r"size_bytes=(\d+).*?max_(?:download_)?bytes=(\d+)", lower_message)
    if key_value_match:
        return int(key_value_match.group(1)), int(key_value_match.group(2))

    ytdlp_match = re.search(
        r"file is larger than max-filesize\s*\(([^>]+)>\s*([^)]+)\)",
        raw_message,
        re.IGNORECASE,
    )
    if ytdlp_match:
        size_bytes = parse_human_size(ytdlp_match.group(1))
        max_bytes = parse_human_size(ytdlp_match.group(2))
        if size_bytes is not None and max_bytes is not None:
            return size_bytes, max_bytes

    return None


def parse_human_size(raw_value: str) -> int | None:
    match = re.search(r"([\d.]+)\s*([kmgt]?i?b|bytes?)", raw_value.strip(), re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "b": 1,
        "byte": 1,
        "bytes": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    multiplier = multipliers.get(unit)
    if multiplier is None:
        return None
    return int(value * multiplier)


def format_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def classify_transcript_error(error: Exception) -> UserErrorMessage:
    message = str(error).lower()

    if "http error 429" in message or "too many requests" in message:
        return UserErrorMessage(
            "Video inviato, ma YouTube ha limitato la trascrizione.",
            "Ho riprovato piu volte a recuperare sottotitoli/trascrizione, ma YouTube ha risposto Too Many Requests. "
            "Riprova piu tardi: il video e' gia in cache, quindi non dovro' riscaricarlo.",
        )

    if "timed out" in message or "timeout" in message:
        return UserErrorMessage(
            "Video inviato, ma la trascrizione e andata in timeout.",
            "Il video e' stato caricato correttamente, pero' il recupero dei sottotitoli ha impiegato troppo tempo.",
        )

    return UserErrorMessage(
        "Video inviato, ma non ho recuperato la trascrizione.",
        "Il riassunto non e partito per un errore durante il recupero dei sottotitoli. Controlla i log con l'ID qui sotto.",
    )
