# WhatsApp Phishing Agent — Design (PoC)

Data: 2026-10-24
Stato: approvato e implementato. Esteso da `2026-10-24-meta-channel-haiku-design.md` (canale Meta, Haiku, conversazione).

## 1. Scopo

Un agente che aiuta un gruppo ristretto di persone a capire se un messaggio ricevuto è un tentativo di phishing o di impersonation. L'utente inoltra su WhatsApp lo screenshot (o il testo) del messaggio sospetto; l'agente lo analizza con un LLM multimodale e risponde con un verdetto chiaro e le azioni consigliate, tenendo l'utente aggiornato durante l'elaborazione.

È una PoC: l'obiettivo è dimostrare il flusso end-to-end e la qualità del giudizio dell'LLM, non la robustezza di produzione.

## 2. Obiettivi, non-obiettivi, anti-obiettivi

**Obiettivi**
- Ricevere screenshot e testo via WhatsApp (Twilio) e analizzarli con LangGraph + Claude Sonnet 5.5.
- Rispondere solo ai numeri presenti in una whitelist JSON.
- Inviare messaggi di avanzamento e un messaggio finale con verdetto, motivazioni e cosa fare.
- Log strutturati ricchi, che permettano di seguire ogni analisi dall'arrivo del webhook alla risposta.

**Non-obiettivi (per ora)**
- Controlli esterni su URL/domini (Safe Browsing, VirusTotal, WHOIS): solo giudizio LLM.
- Conversazioni di follow-up: ogni messaggio in ingresso è un'analisi indipendente, senza memoria.
- Multilingua: risposte solo in italiano (vedi `docs/language-detection.md` per l'evoluzione).
- Persistenza su database, deploy in cloud, interfaccia di amministrazione.

**Anti-obiettivi (sarebbe un fallimento anche se "funziona")**
- Rispondere, anche solo con un errore, a un numero non in whitelist.
- Aprire, visitare o risolvere gli URL presenti negli screenshot.
- Dare un verdetto "legittimo" con tono rassicurante quando l'evidenza è debole: nel dubbio il verdetto è "sospetto" o "indeterminato".
- Salvare su disco screenshot o contenuto dei messaggi.

## 3. Vincoli

- Python 3.12, gestione progetto e dipendenze con `uv`.
- LangGraph per l'agente, FastAPI per il server, Twilio WhatsApp (sandbox per la PoC; numero WhatsApp Business disponibile e attivabile solo cambiando configurazione).
- Modello: `claude-sonnet-5-5` chiamato con l'SDK ufficiale `anthropic` (`messages.parse` + Pydantic) dentro i nodi LangGraph. Non si usa `langchain-anthropic`: il suo `with_structured_output` forza `tool_choice` e su Sonnet 5.5 un `tool_choice` forzato o una `temperature` non di default restituiscono 400.
- Esecuzione in locale, esposta con ngrok.
- Twilio chiude la richiesta webhook dopo 15 secondi: l'analisi deve girare dopo la risposta al webhook.

## 4. Architettura

```
Utente WhatsApp
   │
Twilio (sandbox o numero Business)
   │  POST application/x-www-form-urlencoded
   │  From, Body, NumMedia, MediaUrl{i}, MediaContentType{i}, MessageSid
   ▼
FastAPI  POST /webhooks/twilio/whatsapp
   1. verifica X-Twilio-Signature           ── invalida ──▶ 403
   2. controllo whitelist sul numero From   ── assente ───▶ 200 TwiML vuoto, nessun messaggio
   3. dedupe su MessageSid                  ── duplicato ─▶ 200 TwiML vuoto
   4. risposta immediata 200 TwiML vuoto
   5. BackgroundTask: AnalysisRunner
                         │
                         ├─ lock per utente (analisi sequenziali per lo stesso numero)
                         ├─ invia "ricevuto"
                         ├─ scarica i media da Twilio (basic auth)
                         ├─ esegue il grafo LangGraph (stream_mode="custom")
                         │     └─ eventi di avanzamento ──▶ messaggi WhatsApp
                         └─ invia la risposta finale (o un messaggio di errore)
```

Note su Twilio/WhatsApp:
- WhatsApp consegna ogni immagine come messaggio separato, quindi in pratica un webhook contiene al massimo un'immagine. Se `NumMedia > 1`, le immagini dello stesso messaggio vengono analizzate insieme come un unico caso.
- Con un'immagine, `Body` contiene l'eventuale didascalia, passata all'LLM come contesto.
- Le risposte sono sempre dentro la finestra di 24 ore aperta dal messaggio dell'utente, quindi non servono template approvati.
- Limite pratico di lunghezza di un messaggio: 1600 caratteri; il messaggio finale resta sotto i 1500.

## 5. Componenti

### 5.1 Configurazione (`app/config.py`)
`pydantic-settings`, letta da `.env`. Variabili (in `.env.example`):

| Variabile | Esempio | Note |
|---|---|---|
| `ANTHROPIC_API_KEY` | | obbligatoria |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | |
| `TWILIO_ACCOUNT_SID` | | obbligatoria |
| `TWILIO_AUTH_TOKEN` | | obbligatoria, usata anche per firma e download media |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` | sandbox; per il Business basta cambiarla |
| `PUBLIC_BASE_URL` | `https://xxxx.ngrok-free.app` | URL pubblico usato per validare la firma |
| `VALIDATE_TWILIO_SIGNATURE` | `true` | disattivabile solo per test locali |
| `WHITELIST_PATH` | `config/whitelist.json` | |
| `ANALYSIS_TIMEOUT_SECONDS` | `90` | |
| `DEBUG` | `false` | abilita `/debug/analyze` |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `console` | `console` (leggibile) o `json` |
| `LOG_FILE` | `logs/app.jsonl` | opzionale, sempre in JSON |
| `LOG_CONTENT` | `false` | se `true` logga anche testo estratto e didascalie |
| `LANGSMITH_TRACING` | `false` | opzionale, vedi §7 |
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | | opzionali |

### 5.2 Whitelist (`config/whitelist.json`)
File JSON gestito a mano; l'applicazione lo legge e non lo modifica mai.

```json
{
  "allowed_numbers": [
    { "number": "+393331234567", "name": "Davide" },
    { "number": "+393409876543", "name": "Tester" }
  ]
}
```

- Numeri in formato E.164. Il prefisso `whatsapp:` del campo `From` viene rimosso prima del confronto.
- `name` serve solo ai log, per riconoscere chi ha scritto senza stampare il numero in chiaro.
- Il file viene riletto quando cambia la sua data di modifica: si aggiungono numeri senza riavviare.
- Se il file manca o non è valido, l'applicazione non parte (fail closed).
- Nel repo si committa `config/whitelist.example.json`; `config/whitelist.json` è in `.gitignore`.

### 5.3 Webhook (`app/api/twilio_webhook.py`)
Esegue i 5 passi del §4. Per validare la firma ricostruisce l'URL come `PUBLIC_BASE_URL + path`, perché dietro ngrok l'host visto da FastAPI è diverso da quello firmato da Twilio. Ai numeri non in whitelist non arriva mai nessun messaggio.

### 5.4 Client Twilio (`app/channels/twilio_client.py`)
- `send_message(to, body)`: SDK `twilio` (sincrono) chiamato con `asyncio.to_thread`.
- `download_media(url)`: `httpx` con basic auth (SID, token) e `follow_redirects=True`. Accetta solo `image/jpeg`, `image/png` e `image/webp`, fino a 3,75 MB grezzi (in base64 restano sotto i 5 MB per immagine dell'API Claude).
- Ogni invio passa `status_callback` verso `POST /webhooks/twilio/status`, che logga i cambi di stato (`twilio.status.updated`). Vedi `docs/twilio.md`.

### 5.5 Runner (`app/agent/runner.py`)
- Dedupe in memoria dei `MessageSid` con TTL di 10 minuti.
- Un `asyncio.Lock` per numero: un secondo screenshot inviato durante un'analisi aspetta la fine della prima.
- Genera un `analysis_id` (uuid corto) e lo lega ai log come contesto.
- Applica un timeout globale (`ANALYSIS_TIMEOUT_SECONDS`).
- Traduce gli eventi custom del grafo in messaggi WhatsApp.
- Gestisce gli errori: media non supportato, download fallito, errore o timeout dell'LLM. In ogni caso l'utente riceve un messaggio, e l'errore viene loggato con l'`analysis_id`.

### 5.6 Grafo LangGraph (`app/agent/graph.py`)

```
START ─▶ extract ─┬─ content_kind == "conversazione" ─▶ help ──────────▶ END
                  └─ altrimenti ─────────────────────▶ judge ─▶ format ─▶ END
```

**State** (TypedDict): `analysis_id`, `caption`, `images` (lista di `{mime, b64}`), `extraction`, `verdict`, `reply_text`.

**extract** (LLM con vision, structured output `Extraction`):
- `content_kind`: `messaggio_da_analizzare` | `conversazione`. Serve a distinguere un "ciao" da un SMS incollato.
- `channel`: sms | email | whatsapp | social | telefonata | altro | sconosciuto
- `sender` (mittente visibile), `claimed_brand` (chi dice di essere)
- `text` (testo trascritto), `urls`, `phone_numbers`, `call_to_action`
- `summary` (una frase, usata nel messaggio di avanzamento)

A fine nodo emette l'evento custom `extracted`, con il riassunto, tramite `get_stream_writer()`.

**judge** (LLM, structured output `Verdict`):
- `label`: `phishing` | `sospetto` | `probabilmente_legittimo` | `indeterminato`
- `confidence`: `bassa` | `media` | `alta`
- `red_flags` (lista), `legit_signals` (lista), `recommended_actions` (lista), `explanation` (2-3 frasi)

Il prompt chiede di valutare in modo esplicito: dominio dei link rispetto al brand dichiarato (lookalike, typosquatting, punycode, shortener), coerenza del mittente, urgenza o minacce, richieste di credenziali/pagamenti/OTP, errori linguistici, canale insolito per quel brand. Regola: nel dubbio non usare `probabilmente_legittimo`.

**format** (Python puro, senza LLM): costruisce il messaggio finale in italiano a partire da `Verdict`.

**help**: messaggio breve che spiega cosa inviare.

Il modello LLM viene iniettato (factory in `app/agent/llm.py`), così nei test si sostituisce con un finto. Effort: `low` per `extract`, `medium` per `judge`. È attivo il fallback server-side (`fallbacks="default"`) e `stop_reason == "refusal"` produce un messaggio dedicato all'utente.

Se arriva un messaggio senza testo e senza media, il runner risponde con l'help senza chiamare l'LLM.

### 5.7 Endpoint di servizio
- `GET /health`: stato, versione e numero di voci in whitelist.
- `POST /debug/analyze` (solo con `DEBUG=true`): upload multipart di un'immagine e di una didascalia opzionale. Esegue lo stesso grafo e restituisce JSON con estrazione, verdetto, testo della risposta ed eventi emessi. Serve a provare l'agente senza WhatsApp.

## 6. Messaggi all'utente

1. Alla ricezione: `📥 Ricevuto! Analizzo il messaggio, ci vorrà qualche secondo…`
2. Dopo `extract`: `🔎 Sembra un SMS che si presenta come "Poste Italiane". Controllo link, mittente e contenuto…`
3. Finale:
```
🔴 PHISHING — confidenza alta

Perché:
• Il link porta a poste-italiane-verifica.top, non a poste.it
• Chiede di "sbloccare il conto" entro 24 ore
• Richiede credenziali e codice OTP

Cosa fare:
• Non cliccare il link e non rispondere
• Blocca il mittente
• Se hai già inserito dati, contatta subito la tua banca
```

Etichette: 🔴 PHISHING, 🟠 SOSPETTO, 🟢 PROBABILMENTE LEGITTIMO, ⚪ NON DETERMINABILE.

Errori: `⚠️ Posso analizzare solo immagini (screenshot) o testo.` · `⚠️ Non sono riuscito a completare l'analisi, riprova tra poco.`

## 7. Osservabilità

### 7.1 Log strutturati (`structlog`)
- Formato `console` colorato per lo sviluppo, `json` per l'analisi; su `LOG_FILE` sempre JSON lines.
- Contesto legato con `contextvars` su ogni riga dell'analisi: `analysis_id`, `message_sid`, `user` (nome dalla whitelist + numero mascherato `+39***4567`).
- I log non contengono mai le immagini né il token. Il testo estratto e le didascalie compaiono solo con `LOG_CONTENT=true`.

Catalogo eventi (nome = campo `event`):

| Evento | Campi principali |
|---|---|
| `webhook.received` | message_sid, num_media, has_body |
| `webhook.signature_invalid` | path |
| `webhook.not_whitelisted` | user_masked |
| `webhook.duplicate` | message_sid |
| `analysis.queued` / `analysis.started` | analysis_id, wait_ms |
| `media.downloaded` / `media.rejected` | mime, bytes, ms / reason |
| `graph.node.started` / `graph.node.completed` | node, ms |
| `llm.call.completed` | node, model, input_tokens, output_tokens, ms |
| `extraction.result` | content_kind, channel, claimed_brand, n_urls, n_phones |
| `verdict.result` | label, confidence, n_red_flags |
| `twilio.message.sent` / `twilio.message.failed` | kind (ack/progress/final/error), twilio_sid, ms |
| `analysis.completed` / `analysis.failed` | total_ms, error_type |

Ogni richiesta HTTP genera anche `http.request` con metodo, path, status e durata.

### 7.2 Tracing LangSmith (opzionale, consigliato)
LangGraph manda automaticamente a LangSmith ogni run (nodi, prompt, output, token, latenza) se sono impostate `LANGSMITH_TRACING=true` e `LANGSMITH_API_KEY`. Le chiamate a Claude sono decorate con `@traceable` e il runner passa `analysis_id` come metadata. Guida completa in `docs/langsmith.md`. È il modo più rapido per capire perché il modello ha dato un certo verdetto e per migliorare i prompt. Attenzione: le immagini e il testo degli screenshot finiscono su LangSmith. Per la PoC con dati di test va bene; con dati reali si può impostare `LANGSMITH_HIDE_INPUTS=true`. Default: disattivato.

## 8. Struttura del progetto

```
.
├── pyproject.toml            # uv
├── .env.example
├── .gitignore
├── README.md                 # setup, ngrok, sandbox Twilio, runbook
├── config/
│   └── whitelist.example.json
├── app/
│   ├── main.py               # FastAPI app, lifespan, middleware di log
│   ├── config.py
│   ├── logging.py            # setup structlog, mascheramento numeri
│   ├── whitelist.py
│   ├── media.py              # validazione immagini
│   ├── api/
│   │   ├── twilio_webhook.py
│   │   ├── health.py
│   │   └── debug.py
│   ├── channels/
│   │   ├── twilio_security.py  # firma + parsing webhook
│   │   └── twilio_client.py    # invio messaggi + download media
│   └── agent/
│       ├── state.py
│       ├── schemas.py        # Extraction, Verdict
│       ├── prompts.py
│       ├── llm.py
│       ├── nodes.py
│       ├── graph.py
│       ├── formatting.py
│       └── runner.py
├── tests/
├── evals/
│   ├── samples/              # screenshot di test (non committati se reali)
│   ├── labels.json
│   └── run_eval.py
└── docs/
```

Comandi: `uv sync`, `uv run uvicorn app.main:create_app --factory --reload --port 8000`, `ngrok http 8000`, `uv run pytest`, `uv run ruff check`.

## 9. Test

- Unit: whitelist (parsing, normalizzazione, reload, fail closed), firma Twilio, dedupe, lock, formatting del messaggio finale per ogni etichetta.
- Grafo: LLM finto iniettato. Verifica il routing (conversazione verso help, messaggio verso judge) e gli eventi custom emessi.
- Webhook: `TestClient` con firma valida o invalida, numero in whitelist o no, client Twilio finto. Verifica che a un numero non in whitelist non parta nessun messaggio.
- Eval manuale: `evals/run_eval.py` esegue il grafo reale su `evals/samples` e confronta i risultati con `labels.json`. Gli screenshot li fornisce l'utente: 10-20 tra phishing e legittimi.

## 10. Piani di implementazione

1. **Core agent**: scheletro uv, config, logging, schemi, prompt, grafo, `/health`, `/debug/analyze`, test. Fatto quando: uno screenshot caricato con curl restituisce un verdetto sensato.
2. **Layer Twilio**: whitelist, webhook, firma, download media, runner (dedupe, lock, timeout, messaggi di avanzamento), test. Fatto quando: dalla sandbox WhatsApp si riceve ack, avanzamento e verdetto, e un numero non in whitelist non riceve nulla.
3. **Rifinitura**: gestione errori completa, README e runbook, eval. Fatto quando: l'eval gira e il README basta a un'altra persona per avviare tutto.

## 11. Scelte fatte da Claude (da verificare)

- Formato della whitelist con `name` per i log, reload a caldo, fail closed.
- Firma Twilio verificata prima della whitelist; numeri non autorizzati: 200 vuoto e nessun messaggio.
- `content_kind` in `extract` per gestire i messaggi che non sono da analizzare (es. "ciao") con un help invece di un verdetto.
- Messaggio finale costruito da template Python, non dall'LLM: formato stabile e nessuna chiamata in più.
- Tre messaggi per analisi (ricevuto, avanzamento, finale).
- Dedupe e lock solo in memoria: con un singolo processo locale bastano.
- `structlog` per i log; LangSmith opzionale e spento di default.
- SDK `anthropic` diretto al posto di `langchain-anthropic` (vedi §3).
- Endpoint di status callback per seguire nei log il ciclo di vita dei messaggi inviati.
- Layout `app/` piatto invece del layout `src/`.
