# Videogram

Bot Telegram containerizzato che intercetta link social in chat e li ripubblica come contenuti nativi Telegram.
Quando il post contiene media, Videogram prova a inviare video, foto, gallery o audio; quando trova solo testo, lo ripubblica come messaggio.

Supporta:

- `youtube.com/watch?v=...`
- `youtu.be/...`
- `youtube.com/shorts/...`
- `youtube.com/embed/...`
- `reddit.com/r/.../comments/...`
- `old.reddit.com/r/.../comments/...`
- `redd.it/...`
- `instagram.com/reel/...`
- `instagram.com/p/...`
- `instagram.com/tv/...`
- `facebook.com/watch/?v=...`
- `facebook.com/reel/...`
- `fb.watch/...`
- `threads.net/@.../post/...`
- `threads.com/t/...`
- `x.com/.../status/...`
- `twitter.com/.../status/...`
- `tiktok.com/@.../video/...`
- `vm.tiktok.com/...`
- `vt.tiktok.com/...`

Per alcuni social il supporto non-video e' best effort: se `yt-dlp` o un fallback dedicato espone immagini/gallery/audio/testo, Videogram li invia come foto, media group, audio o testo Telegram. Nei post TikTok photo mode, se disponibile, invia anche la musica di accompagnamento come audio separato. Per i contenuti video troppo grandi, Videogram prova formati progressivamente piu piccoli prima di arrendersi; se nessun video rientra nel limite, prova anche audio-only. Se aumenti `MAX_DOWNLOAD_MB`, i media gia' in cache scaricati con un limite piu basso vengono rivalutati una volta per provare a ottenere una qualita' migliore; se non cambia nulla, la cache viene marcata come gia' verificata per il nuovo limite.

Nota: il supporto Threads usa un plugin `yt-dlp` esterno, installato da GitHub e bloccato a commit specifico in `requirements.txt`.

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

4. Aggiungi il bot a una chat o scrivigli in privato, poi manda un link supportato.

Nei gruppi Telegram potresti dover disattivare la privacy mode del bot da BotFather, altrimenti il bot non riceve tutti i messaggi normali della chat.

## Configurazione

| Variabile | Default | Descrizione |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | obbligatoria | Token del bot Telegram |
| `TELEGRAM_API_ID` | vuota | API ID da `my.telegram.org`, usato solo dal Bot API server locale |
| `TELEGRAM_API_HASH` | vuota | API hash da `my.telegram.org`, usato solo dal Bot API server locale |
| `TELEGRAM_API_BASE_URL` | vuota | Base URL Bot API custom, ad esempio `http://telegram-bot-api:8081/bot` |
| `TELEGRAM_API_FILE_BASE_URL` | vuota | Base URL file Bot API custom, ad esempio `http://telegram-bot-api:8081/file/bot` |
| `TELEGRAM_LOCAL_MODE` | `false` | Abilita la modalita locale del client Telegram quando usi il Bot API server self-hosted |
| `ALLOWED_CHAT_IDS` | vuota | Lista di gruppi/supergruppi Telegram autorizzati, separati da virgola. Vuota = tutte le chat di gruppo autorizzate |
| `ALLOWED_USER_IDS` | vuota | Lista di utenti Telegram autorizzati a usare il bot in privato, separati da virgola. Vuota = tutti gli utenti autorizzati in privato |
| `USAGE_ALLOWED_USER_IDS` | vuota | Lista separata di utenti autorizzati a usare `/usage` in privato. Vuota = nessun utente autorizzato |
| `COOKIE_ALLOWED_USER_IDS` | vuota | Lista separata di utenti autorizzati a usare `/cookie` in privato. Vuota = eredita `USAGE_ALLOWED_USER_IDS` |
| `USAGE_REPORT_USER_ID` | vuota | User ID Telegram a cui inviare alert periodici di utilizzo in privato |
| `USAGE_CHECK_INTERVAL_MINUTES` | `60` | Ogni quanti minuti controllare le soglie di utilizzo |
| `USAGE_ALERT_STEP_PERCENT` | `10` | Soglia incrementale traffico Hetzner per alert: 10 = 10%, 20%, 30%... |
| `USAGE_ALERT_STATE_FILE` | `/var/log/videogram/usage-alerts.json` | Stato locale delle soglie gia notificate |
| `HETZNER_API_TOKEN` | vuota | Token Hetzner Cloud API per leggere le metriche traffico |
| `HETZNER_SERVER_ID` | vuota | ID server Hetzner Cloud da monitorare |
| `HETZNER_MONTHLY_TRAFFIC_TB` | `20` | Traffico mensile incluso da usare per calcolare la percentuale |
| `OPENAI_ADMIN_KEY` | vuota | Admin key OpenAI per leggere i costi organizzazione da `/v1/organization/costs` |
| `OPENAI_MONTHLY_BUDGET_USD` | `0` | Budget mensile OpenAI per calcolare percentuali e alert. `0` = mostra costi ma niente alert percentuali |
| `OPENAI_API_KEY` | vuota | API key OpenAI per generare riassunti da trascrizioni o descrizioni. Vuota = riassunti disattivati |
| `OPENAI_SUMMARY_MODEL` | `gpt-5-mini` | Modello OpenAI usato per i riassunti |
| `OPENAI_SUMMARY_PROMPT` | vedi `.env.example` | Prompt usato per trasformare il testo disponibile in riassunto |
| `OPENAI_SUMMARY_MAX_TRANSCRIPT_CHARS` | `20000` | Numero massimo di caratteri inviati a OpenAI |
| `SUMMARY_TRANSCRIPT_LANGS` | `it,en` | Lingue preferite per sottotitoli/trascrizioni, separate da virgola |
| `MAX_DOWNLOAD_MB` | `512` | Limite massimo del file scaricato e salvato in cache |
| `MAX_TELEGRAM_UPLOAD_MB` | `48` | Limite massimo del file inviato tramite Bot API pubblico. Deve restare sotto i 50 MB di Telegram |
| `DOWNLOAD_DIR` | `/tmp/videogram-downloads` | Cartella temporanea nel container |
| `MIN_FREE_DISK_PERCENT` | `5` | Spazio libero minimo da mantenere nella cache locale |
| `MAX_CONCURRENT_JOBS` | `2` | Numero massimo di link processati contemporaneamente; gli altri restano in coda |
| `SITE_CONCURRENT_JOBS` | `1` | Numero massimo di link processati contemporaneamente per singolo sito, sopra la coda globale |
| `LOG_LEVEL` | `INFO` | Livello log Python |
| `LOG_FILE` | `/var/log/videogram/videogram.log` | File log persistente nel container |
| `LOG_MAX_MB` | `10` | Dimensione massima di ogni file log prima della rotazione |
| `LOG_BACKUP_COUNT` | `5` | Numero di file log ruotati da conservare |
| `FAILED_LINKS_FILE` | `/var/log/videogram/failed-links.jsonl` | Storico JSONL dei link falliti, utile per debug e miglioramento matcher |
| `YTDLP_COOKIES_FILE` | vuota | File cookies Netscape da passare a `yt-dlp` per piattaforme che richiedono login/verifica |
| `YTDLP_COOKIES_DIR` | `/cookies` | Cartella con file cookies `.txt` selezionati per dominio da `yt-dlp`, ad esempio `youtube.txt`, `reddit.txt`, `instagram.txt`, `facebook.txt`, `threads.txt`, `x.txt` e `tiktok.txt` |

I media scaricati vengono tenuti nella cartella locale `./downloads` e riusati quando viene richiesto di nuovo lo stesso URL normalizzato. Quando lo spazio libero scende sotto `MIN_FREE_DISK_PERCENT`, Videogram elimina prima i file meno usati recentemente.
Per i video, Videogram salva anche una miniatura `thumbnail.jpg` nella cache e la passa a Telegram durante l'upload, cosi i client hanno un'anteprima anche quando il file non parte automaticamente. Se la piattaforma espone gia una thumbnail, usa quella; altrimenti estrae un frame dal video.

`MAX_CONCURRENT_JOBS` limita il numero di link processati in parallelo. `SITE_CONCURRENT_JOBS` limita in parallelo anche ogni singolo sito, per esempio un solo download Instagram alla volta. Se arrivano piu link insieme, Videogram risponde subito e mette le richieste eccedenti nella coda generale o nella coda del sito.

`/usage` funziona solo in chat privata e solo per gli ID elencati in `USAGE_ALLOWED_USER_IDS`. Se configuri `USAGE_REPORT_USER_ID`, Videogram controlla periodicamente traffico Hetzner e costi OpenAI. Manda un alert privato ogni volta che viene superato un nuovo multiplo di `USAGE_ALERT_STEP_PERCENT`; per OpenAI serve anche impostare `OPENAI_MONTHLY_BUDGET_USD`.

Il limite `MAX_TELEGRAM_UPLOAD_MB` tiene margine rispetto al limite pubblico di upload dei bot Telegram, pari a circa 50 MB. Se un media in cache supera questo limite, Videogram lo rifiuta prima dell'upload invece di far arrivare un errore `413` da Telegram. Con il Bot API server locale puoi alzare questo valore, per esempio a `1900`.

Il container include Node.js come runtime JavaScript per permettere a `yt-dlp` di risolvere eventuali challenge JavaScript/EJS.

## Bot API server locale

Per superare il limite pubblico di upload da circa 50 MB puoi usare il Bot API server locale. Rimane dentro la rete Docker e non espone porte su internet.

1. Vai su https://my.telegram.org, fai login con il tuo numero Telegram, entra in **API development tools** e crea una app.
2. Copia `api_id` e `api_hash` nel `.env`:

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef123456...
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081/bot
TELEGRAM_API_FILE_BASE_URL=http://telegram-bot-api:8081/file/bot
TELEGRAM_LOCAL_MODE=true
MAX_TELEGRAM_UPLOAD_MB=1900
```

3. Prima migrazione una tantum: se il bot stava usando `api.telegram.org`, esegui `logOut` sul Bot API pubblico:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/logOut"
```

4. Avvia includendo il profile del server locale:

```bash
docker compose --profile local-bot-api up -d --build
```

Il servizio locale usa `./telegram-bot-api-data` per i propri dati ed e' ignorato da Git. Non pubblicare la porta `8081` verso internet: Videogram ci parla direttamente via rete Docker interna.

## Riassunti

Se `OPENAI_API_KEY` e' configurata, Videogram prova a recuperare una trascrizione o sottotitolo tramite `yt-dlp` mentre prepara il contenuto. Quando la trascrizione esiste, invia un secondo messaggio con un riassunto generato via OpenAI Responses API. Se non trova una trascrizione ma il post ha una descrizione o testo, prova a riassumere quello.

I riassunti vengono salvati nella stessa cache locale del contenuto. La cache viene riusata solo se URL normalizzato, modello, prompt, tipo di testo e testo di partenza coincidono, cosi non spendi token quando lo stesso post viene richiesto di nuovo.

Esempio:

```env
OPENAI_API_KEY=sk-...
OPENAI_SUMMARY_MODEL=gpt-5-mini
OPENAI_SUMMARY_PROMPT=Riassumi in italiano in pochi punti concisi, con eventuali nomi e numeri importanti.
SUMMARY_TRANSCRIPT_LANGS=it,en
```

## Whitelist chat e utenti

Per limitare l'uso del bot a gruppi specifici, imposta `ALLOWED_CHAT_IDS` nel `.env`:

```env
ALLOWED_CHAT_IDS=-1001234567890,-1009876543210
```

In una chat autorizzata, Videogram risponde ai link postati da qualsiasi utente della chat. Se invece qualcuno scrive al bot in privato, viene controllata `ALLOWED_USER_IDS`:

```env
ALLOWED_USER_IDS=111111111,222222222
```

Gli ID delle chat di gruppo o supergruppo sono spesso negativi e iniziano con `-100`. Gli ID utente sono di solito positivi. Se non conosci un ID, lascia temporaneamente le whitelist vuote, manda un messaggio al bot dalla chat o dall'utente interessato e leggi `chat_id=...` e `user_id=...` nei log. Poi aggiorna `.env` e riavvia:

```bash
docker compose up -d --build
```

Quando `ALLOWED_CHAT_IDS` e' attiva, Videogram ignora le chat di gruppo non autorizzate. Se viene aggiunto a un gruppo fuori lista, invia un breve avviso e prova a uscire automaticamente. Quando `ALLOWED_USER_IDS` e' attiva, Videogram risponde in privato solo agli utenti autorizzati.

## Cookies

Alcune piattaforme possono richiedere una sessione autenticata o applicare controlli anti-bot. In questi casi esporta i cookies in formato Netscape e salvali localmente, per esempio:

- `./cookies/youtube.txt`
- `./cookies/reddit.txt`
- `./cookies/instagram.txt`
- `./cookies/facebook.txt`
- `./cookies/threads.txt`
- `./cookies/x.txt`
- `./cookies/tiktok.txt`

Poi aggiungi al tuo `.env`:

```env
YTDLP_COOKIES_DIR=/cookies
```

La cartella `./cookies` è ignorata da Git e copiata in una posizione temporanea a ogni download, così `yt-dlp` non modifica il file originale. È montata in scrittura solo per permettere agli utenti autorizzati di aggiornare i cookie via Telegram.

Per aggiornare un file cookie da Telegram, usa il bot in privato:

```text
/cookie instagram <contenuto del cookies.txt Netscape>
```

Oppure invia `/cookie instagram` in reply a un file `.txt` o a un messaggio che contiene i cookie in formato Netscape. Il comando funziona solo per `COOKIE_ALLOWED_USER_IDS`; se questa variabile è vuota, eredita `USAGE_ALLOWED_USER_IDS`.

Dopo aver modificato `.env`:

```bash
docker compose up -d --build
```

## Log

```bash
docker compose logs -f
```

I log vengono scritti anche nella cartella locale `./logs` con rotazione automatica. Per leggerli dal container:

```bash
docker compose exec videogram tail -f /var/log/videogram/videogram.log
```

Ogni link processato ha un `request_id`, utile per seguire download, cache, upload ed eventuali errori nello stesso flusso.

I fallimenti vengono salvati anche in `./logs/failed-links.jsonl`, uno per riga, con URL normalizzato, piattaforma, fase fallita, tipo errore e `request_id`.

## Roadmap naturale

- Aggiungere altri siti creando nuovi normalizzatori in `app/links.py`.
- Migliorare la scelta formato/qualità in `app/downloader.py`.
