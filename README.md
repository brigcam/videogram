# Videogram

Bot Telegram containerizzato che intercetta link video in chat e li ripubblica come video nativi Telegram.

Per ora supporta YouTube:

- `youtube.com/watch?v=...`
- `youtu.be/...`
- `youtube.com/shorts/...`
- `youtube.com/embed/...`

## Avvio rapido

1. Crea un bot con BotFather e copia il token.
2. Prepara la configurazione:

```bash
cp .env.example .env
nano .env
```

3. Avvia il container:

```bash
docker compose up -d --build
```

4. Aggiungi il bot a una chat o scrivigli in privato, poi manda un link YouTube.

Nei gruppi Telegram potresti dover disattivare la privacy mode del bot da BotFather, altrimenti il bot non riceve tutti i messaggi normali della chat.

## Configurazione

| Variabile | Default | Descrizione |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | obbligatoria | Token del bot Telegram |
| `MAX_DOWNLOAD_MB` | `48` | Limite massimo del file scaricato |
| `DOWNLOAD_DIR` | `/tmp/videogram-downloads` | Cartella temporanea nel container |
| `MIN_FREE_DISK_PERCENT` | `5` | Spazio libero minimo da mantenere nella cache locale |
| `LOG_LEVEL` | `INFO` | Livello log Python |

I video scaricati vengono tenuti nella volume Docker `videogram-downloads` e riusati quando viene richiesto di nuovo lo stesso URL normalizzato. Quando lo spazio libero scende sotto `MIN_FREE_DISK_PERCENT`, Videogram elimina prima i file meno usati recentemente.

Il limite `MAX_DOWNLOAD_MB` è conservativo perché i bot Telegram possono avere limiti di upload diversi a seconda della modalità/API usata. Puoi aumentarlo, ma se Telegram rifiuta l'upload conviene ridurlo o passare più avanti a un uploader basato su client MTProto.

## Log

```bash
docker compose logs -f
```

## Roadmap naturale

- Aggiungere altri siti creando nuovi normalizzatori in `app/links.py`.
- Migliorare la scelta formato/qualità in `app/downloader.py`.
- Aggiungere una coda per evitare troppi download simultanei nelle chat grandi.
- Salvare metriche o storico minimo dei link falliti per migliorare i matcher.
