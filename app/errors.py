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
        return UserErrorMessage(
            "Il video supera il limite configurato.",
            "Aumenta MAX_DOWNLOAD_MB oppure prova un video piu piccolo.",
        )

    if "requested format is not available" in message or "no video formats found" in message:
        return UserErrorMessage(
            "Non ho trovato un formato video scaricabile.",
            "Il sito non sta offrendo un MP4 compatibile o yt-dlp non riesce a estrarre questo contenuto.",
        )

    if "unsupported url" in message:
        return UserErrorMessage(
            "Questo link non e supportato.",
            "Per ora Videogram gestisce solo link YouTube riconosciuti.",
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
