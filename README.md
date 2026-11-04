# WhatsApp Phishing Agent (PoC)

> **English summary.** A proof-of-concept WhatsApp bot that helps people spot phishing and impersonation. Users forward a screenshot (or the text) of a suspicious message; a LangGraph agent powered by Claude reads it, judges it and replies on WhatsApp with progress updates and a verdict plus what to do. Only whitelisted numbers get answers. WhatsApp is reached through **Twilio** (polling or webhook) or the **Meta WhatsApp Cloud API** (webhook). Built with FastAPI, LangGraph, the Anthropic SDK, structlog and uv. Documentation is in Italian.

Agente che aiuta a riconoscere phishing e impersonation. L'utente inoltra su WhatsApp lo screenshot (o il testo) di un messaggio sospetto. L'agente lo analizza con Claude (**Haiku 5.5** di default, Sonnet 5.5 opzionale) e risponde in tre passi: ricezione, avanzamento e verdetto con le azioni consigliate. Risponde solo ai numeri presenti nella whitelist.

Stack: FastAPI, LangGraph, SDK `anthropic`, WhatsApp via **Twilio** oppure **Meta Cloud API**, `structlog`, `uv`.

Documentazione:
- [Come funziona Twilio](docs/twilio.md) (sandbox, polling, webhook, Trial e KYC)
- [Come funziona la WhatsApp Cloud API di Meta](docs/meta-whatsapp.md)
- [LangSmith](docs/langsmith.md) (tracing opzionale)
- [Lingua dell'utente](docs/language-detection.md) (proposta, non implementata)
- Storico di progetto (spec, piani di implementazione): [`docs/superpowers/`](docs/superpowers/). Sono istantanee del momento in cui sono stati scritti: la fonte di verità è il codice.

## Requisiti

- [uv](https://docs.astral.sh/uv/)
- una chiave API Anthropic
- **uno** tra:
  - un account **Twilio a pagamento**, con profilo di compliance (KYC) approvato. In Trial non basta: vedi [docs/twilio.md §12](docs/twilio.md#12-account-trial-e-nuova-tryout-ui) e [§14](docs/twilio.md#14-account-a-pagamento-kyc-e-costi)
  - un'app **Meta** con il numero di test WhatsApp (gratis)
- [ngrok](https://ngrok.com/download), **solo** con `INBOUND_MODE=webhook` (sempre necessario con Meta)

## Setup

```bash
uv sync
cp .env.example .env                                   # compila le chiavi
cp config/whitelist.example.json config/whitelist.json # metti il tuo numero (+39...)
```

`config/whitelist.json` è gestito a mano: l'app lo legge e non lo scrive mai. Se lo modifichi mentre il server gira, viene ricaricato da solo. Se il JSON non è valido, l'app tiene l'ultima versione valida e scrive `whitelist.reload_failed` nei log.

## Scegliere canale e ricezione

Due variabili decidono come il bot parla con WhatsApp. Il resto dell'agente (analisi, whitelist, log, eval) è identico in tutte le combinazioni.

| `CHANNEL` | `INBOUND_MODE` | Come arrivano i messaggi | Serve ngrok? | Guida |
|---|---|---|---|---|
| `twilio` | `polling` (default) | ogni `POLL_INTERVAL_SECONDS` (default 5) il server chiede a Twilio i messaggi nuovi | **No** | [docs/twilio.md §13](docs/twilio.md#13-ricezione-via-polling-senza-webhook) |
| `twilio` | `webhook` | Twilio chiama `/webhooks/twilio/whatsapp` (stati su `/webhooks/twilio/status`) | Sì (`PUBLIC_BASE_URL`) | [docs/twilio.md §9](docs/twilio.md#9-setup-della-sandbox-passo-per-passo) |
| `meta` | `webhook` (obbligatorio) | Meta chiama `/webhooks/meta` | Sì (`PUBLIC_BASE_URL`) | [docs/meta-whatsapp.md §9](docs/meta-whatsapp.md#9-setup-passo-per-passo) |

- Variabili obbligatorie per `twilio`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`.
- Variabili obbligatorie per `meta`: `META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`.
- Se manca qualcosa, oppure con `CHANNEL=meta` + `INBOUND_MODE=polling`, l'app non parte e dice cosa manca.
- Vengono montati solo gli endpoint della combinazione scelta: in polling nessun webhook. `/health` riporta canale e modalità.

Differenze visibili all'utente:
- **Meta:** spunte blu e "sta scrivendo…" appena arriva il messaggio, poi avanzamento e verdetto.
- **Twilio:** messaggio "📥 Ricevuto", poi avanzamento e verdetto.

Il polling è comodo per sviluppare in locale: niente tunnel e niente URL da configurare. In cambio la risposta arriva con qualche secondo di ritardo in più. Gli stati dei messaggi inviati si vedono solo nella Console Twilio, perché senza URL pubblico non c'è callback.

## Avvio

**Twilio in polling (default):**

```bash
uv run uvicorn app.main:create_app --factory --port 8000
```

Nei log devi vedere `app.started inbound_mode=polling` e `poller.started`. Unisciti alla sandbox da WhatsApp con `join <parola>` (vedi [docs/twilio.md §9](docs/twilio.md#9-setup-della-sandbox-passo-per-passo), passi 1-3) e manda uno screenshot.

**Con webhook (Twilio con `INBOUND_MODE=webhook`, oppure Meta):**

```bash
# terminale 1
uv run uvicorn app.main:create_app --factory --port 8000
# terminale 2
ngrok http 8000
```

Copia l'URL `https://….ngrok-free.app` di ngrok in `PUBLIC_BASE_URL` nel `.env` e riavvia il server. Poi configura l'URL del webhook nella Console del canale:
- Twilio: *When a message comes in* = `https://…/webhooks/twilio/whatsapp`
- Meta: *Callback URL* = `https://…/webhooks/meta`

Se riavvii ngrok senza un dominio fisso, l'URL cambia: aggiornalo **sia** nel `.env` **sia** nella Console. Con Twilio, altrimenti, ogni richiesta viene rifiutata con 403 e nei log compare `webhook.signature_invalid` con l'`expected_url` usato.

## Debug senza WhatsApp

Con `DEBUG=true` è attivo un endpoint che esegue lo stesso grafo su un'immagine caricata. Risponde solo alle richieste locali: quelle che arrivano da ngrok, riconoscibili dall'header `X-Forwarded-For`, ricevono 404.

```bash
curl -s -F "image=@screenshot.png" -F "caption=è vero?" localhost:8000/debug/analyze | python -m json.tool
curl -s -F "caption=Il tuo pacco è in giacenza, paga 1,99€ su http://..." localhost:8000/debug/analyze
```

Restituisce l'estrazione, il verdetto, il testo che riceverebbe l'utente e gli eventi di avanzamento. Con `DEBUG=true` è attiva anche la documentazione delle API su `/docs`.

## Leggere i log

Ogni riga di un'analisi porta `analysis_id`, `message_sid` e `user`, cioè il nome dalla whitelist più il numero mascherato (`+39***4567`). Immagini e token non finiscono mai nei log. Il testo estratto compare solo con `LOG_CONTENT=true`.

Eventi principali (non è l'elenco completo):

| Evento | Quando |
|---|---|
| `app.started` | avvio, con canale, modalità di ricezione e modello |
| `whitelist.loaded` / `whitelist.reloaded` / `whitelist.reload_failed` | whitelist letta, riletta dopo una modifica, o modifica non valida (resta l'ultima valida) |
| `inbound.received` | è arrivato un messaggio (`source`: `twilio_webhook`, `twilio_polling`, `meta_webhook`) |
| `inbound.not_whitelisted` | numero non in whitelist: nessuna risposta inviata |
| `inbound.duplicate` | stesso id di messaggio ricevuto due volte |
| `webhook.signature_invalid` | firma non valida: URL ngrok/console non allineati, segreto sbagliato o chiamata non dal provider |
| `webhook.invalid_json` | payload Meta con firma valida ma non JSON |
| `poller.started` / `poller.message_found` / `poller.message_skipped` / `poller.error` / `poller.stopped` | polling Twilio: avvio, nuovo messaggio, messaggio illeggibile saltato, errore temporaneo (si riprova al giro dopo), arresto |
| `analysis.queued` / `analysis.started` | analisi in coda e avviata (`wait_ms` = attesa per il lock utente) |
| `media.downloaded` / `media.rejected` | download dell'immagine dal provider |
| `graph.node.started` / `graph.node.completed` | nodi LangGraph (`extract`, `judge`, `format`, `help`) con durata |
| `llm.call.completed` / `llm.refusal` | chiamata a Claude (token, durata, `stop_reason`) o rifiuto del modello |
| `extraction.result` / `verdict.result` / `verdict.downgraded` | cosa ha letto il modello, cosa ha deciso, e se un "legittimo" con confidenza bassa è stato portato a "non determinabile" |
| `twilio.message.sent` / `twilio.message.failed` | messaggio inviato all'utente (`kind`: ack, progress, final, help, error) |
| `twilio.status.updated` | (solo webhook) Twilio aggiorna lo stato di un messaggio inviato |
| `meta.verified` / `meta.verify_failed` | handshake del webhook Meta riuscito o fallito (verify token) |
| `meta.ack.sent` / `meta.ack.failed` | spunte blu + "sta scrivendo…" (solo Meta) |
| `meta.message.sent` / `meta.message.failed` | messaggio inviato via Graph API (`error_code`: vedi docs/meta-whatsapp.md §10) |
| `meta.status.updated` / `meta.payload.malformed` | Meta aggiorna lo stato di un messaggio; elementi malformati saltati in un payload |
| `analysis.completed` / `analysis.failed` | esito finale; `analysis.failed` riporta `error_type` (es. `timeout`, `refusal`) |
| `http.request` | ogni richiesta HTTP (metodo, path, status, durata) |

Con `LOG_FILE=logs/app.jsonl` ogni evento finisce anche su file in formato JSON lines:

```bash
jq -c 'select(.analysis_id=="a1b2c3d4")' logs/app.jsonl        # tutta un'analisi
jq -c 'select(.event=="llm.call.completed")' logs/app.jsonl    # token e tempi delle chiamate LLM
jq -c 'select(.event=="twilio.status.updated")' logs/app.jsonl # ciclo di vita dei messaggi (webhook)
```

## Test

```bash
uv run pytest            # test offline (nessuna chiamata esterna)
uv run pytest -m live    # chiamata reale a Claude (serve ANTHROPIC_API_KEY, costa pochi centesimi)
uv run ruff check
```

## Valutare l'agente

```bash
cp evals/labels.example.json evals/labels.json   # elenco dei campioni con l'etichetta attesa
# metti gli screenshot in evals/samples/ (cartella ignorata da git)
uv run python -m evals.run_eval                  # chiede conferma: ogni campione = 2 chiamate a Claude
```

Ogni campione ha `file` (uno screenshot in `evals/samples/`) **oppure** `text`, più `expected`: `phishing`, `sospetto`, `probabilmente_legittimo` o `indeterminato`. Gli screenshot citati in `labels.example.json` (`poste_sms.png`, `banca_vera.png`) **non sono inclusi**: forniscili tu, oppure togli quelle righe. Lo script stampa:
- l'esito di ogni campione;
- l'accuratezza (`sospetto` conta come corretto quando l'atteso è `phishing`, perché l'utente deve fare la stessa cosa);
- i **dangerous misses**, cioè truffe giudicate legittime: è il numero da tenere a zero;
- la matrice di confusione.

I risultati vengono salvati in `evals/results/`. Il file di esempio contiene anche un caso di prompt injection, un messaggio che "ordina" all'assistente di rispondere che è legittimo.

## Costi indicativi

Valori misurati durante la PoC (ottobre 2026, destinatario italiano). Le tariffe cambiano: verifica sulle pagine prezzi ufficiali.

| Voce | Costo |
|---|---|
| Claude Haiku 5.5 (~4.000 token in, ~600 out) | ~$0,001 per analisi |
| Claude Sonnet 5.5 (`ANTHROPIC_MODEL=claude-sonnet-5-5`) | ~$0,016 per analisi |
| Twilio sandbox, messaggio inviato | ~$0,03 (commissione Twilio $0,005 + tariffa Meta per l'Italia). Con 3 messaggi: ~$0,09 per analisi |
| Twilio, messaggio in arrivo | $0 nei test con la sandbox |
| Meta Cloud API, numero di test | gratis entro la quota mensile di service messages |

Dettagli e fonti in [docs/twilio.md §14](docs/twilio.md#14-account-a-pagamento-kyc-e-costi).

## LangSmith (opzionale)

Imposta `LANGSMITH_TRACING=true` e `LANGSMITH_API_KEY` nel `.env` per vedere ogni analisi come trace: prompt, immagine, output, token e tempi. Dettagli in [docs/langsmith.md](docs/langsmith.md).

## Limiti noti

- Nessun follow-up: ogni messaggio è un'analisi a sé.
- Risposte solo in italiano (vedi [docs/language-detection.md](docs/language-detection.md)).
- Nessun controllo esterno sugli URL (reputazione, WHOIS): solo il giudizio dell'LLM. Gli URL non vengono mai aperti.
- Duplicati e lock solo in memoria: va bene un singolo processo locale, non più istanze.
- WhatsApp consegna ogni immagine come messaggio separato: più screenshot inviati insieme diventano analisi separate, eseguite in sequenza.

## Licenza

[MIT](LICENSE)
