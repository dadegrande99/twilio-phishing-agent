# WhatsApp Phishing Agent (PoC)

Agente che aiuta a riconoscere phishing e impersonation. L'utente inoltra su WhatsApp lo screenshot (o il testo) di un messaggio sospetto. L'agente lo analizza con Claude Sonnet 5.5 e risponde in tre passi: un messaggio di ricezione, uno di avanzamento e il verdetto con le azioni consigliate. Risponde solo ai numeri presenti nella whitelist.

Stack: FastAPI, LangGraph, SDK `anthropic`, Twilio WhatsApp, `structlog`, `uv`.

Documentazione:
- [Design](docs/superpowers/specs/2026-10-24-whatsapp-phishing-agent-design.md)
- [Piano di implementazione](docs/superpowers/plans/2026-10-24-whatsapp-phishing-agent.md)
- [Come funziona Twilio](docs/twilio.md)
- [LangSmith](docs/langsmith.md)
- [Lingua dell'utente](docs/language-detection.md)

## Requisiti

- [uv](https://docs.astral.sh/uv/)
- una chiave API Anthropic
- un account Twilio (va bene la sandbox WhatsApp)
- [ngrok](https://ngrok.com/download) (`brew install ngrok`, poi `ngrok config add-authtoken <token>`)

## Setup

```bash
uv sync
cp .env.example .env                                   # compila le chiavi
cp config/whitelist.example.json config/whitelist.json # metti il tuo numero (+39...)
```

`config/whitelist.json` è gestito a mano: l'app lo legge e non lo scrive mai. Se lo modifichi mentre il server gira, viene ricaricato da solo. Se il JSON non è valido, l'app tiene l'ultima versione valida e scrive `whitelist.reload_failed` nei log.

## Avvio

```bash
# terminale 1
uv run uvicorn app.main:create_app --factory --port 8000
# terminale 2
ngrok http 8000
```

Copia l'URL `https://….ngrok-free.app` di ngrok in `PUBLIC_BASE_URL` nel `.env` e riavvia il server. Serve per verificare la firma di Twilio.

## Configurare la sandbox Twilio

Guida completa in [docs/twilio.md §9](docs/twilio.md#9-setup-della-sandbox-passo-per-passo). In breve:

1. Console Twilio → **Messaging → Try it out → Send a WhatsApp message**.
2. Dal tuo WhatsApp manda `join <parola>` al numero sandbox.
3. In **Sandbox settings**, alla voce *When a message comes in*, inserisci `https://….ngrok-free.app/webhooks/twilio/whatsapp` con metodo `POST`.
4. Manda uno screenshot al numero sandbox.

Se riavvii ngrok l'URL cambia: aggiornalo **sia** nel `.env` **sia** nella console Twilio. Altrimenti ogni richiesta viene rifiutata con 403 e nei log compare `webhook.signature_invalid` con l'`expected_url` usato.

## Debug senza WhatsApp

Con `DEBUG=true` è attivo un endpoint che esegue lo stesso grafo su un'immagine caricata. Risponde solo alle richieste locali: quelle che arrivano da ngrok, riconoscibili dall'header `X-Forwarded-For`, ricevono 404.

```bash
curl -s -F "image=@screenshot.png" -F "caption=è vero?" localhost:8000/debug/analyze | python -m json.tool
curl -s -F "caption=Il tuo pacco è in giacenza, paga 1,99€ su http://..." localhost:8000/debug/analyze
```

Restituisce l'estrazione, il verdetto, il testo che riceverebbe l'utente e gli eventi di avanzamento. Con `DEBUG=true` è attiva anche la documentazione delle API su `/docs`.

## Leggere i log

Ogni riga di un'analisi porta `analysis_id`, `message_sid` e `user`, cioè il nome dalla whitelist più il numero mascherato (`+39***4567`). Immagini e token non finiscono mai nei log. Il testo estratto compare solo con `LOG_CONTENT=true`.

| Evento | Quando |
|---|---|
| `webhook.received` | Twilio ha consegnato un messaggio (firma valida) |
| `webhook.signature_invalid` | firma non valida: URL ngrok/console non allineati, oppure chiamata non da Twilio |
| `webhook.not_whitelisted` | numero non in whitelist: nessuna risposta inviata |
| `webhook.duplicate` | stesso `MessageSid` ricevuto due volte |
| `analysis.queued` / `analysis.started` | analisi in coda e avviata (`wait_ms` = attesa per il lock utente) |
| `media.downloaded` / `media.rejected` | download dell'immagine da Twilio |
| `graph.node.started` / `graph.node.completed` | nodi LangGraph (`extract`, `judge`, `format`, `help`) con durata |
| `llm.call.completed` | chiamata a Claude: token, durata, `stop_reason` |
| `extraction.result` / `verdict.result` | cosa ha letto il modello e cosa ha deciso |
| `twilio.message.sent` / `twilio.message.failed` | messaggio inviato all'utente (`kind`: ack, progress, final, help, error) |
| `twilio.status.updated` | Twilio aggiorna lo stato di un messaggio inviato (sent, delivered, read, failed + `error_code`) |
| `analysis.completed` / `analysis.failed` | esito finale con `total_ms` |

Con `LOG_FILE=logs/app.jsonl` ogni evento finisce anche su file in formato JSON lines:

```bash
jq -c 'select(.analysis_id=="a1b2c3d4")' logs/app.jsonl        # tutta un'analisi
jq -c 'select(.event=="llm.call.completed")' logs/app.jsonl    # token e tempi delle chiamate LLM
jq -c 'select(.event=="twilio.status.updated")' logs/app.jsonl # ciclo di vita dei messaggi
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

Ogni campione ha `file` (uno screenshot in `evals/samples/`) **oppure** `text`, più `expected`: `phishing`, `sospetto`, `probabilmente_legittimo` o `indeterminato`. Lo script stampa:
- l'esito di ogni campione;
- l'accuratezza (`sospetto` conta come corretto quando l'atteso è `phishing`, perché l'utente deve fare la stessa cosa);
- i **dangerous misses**, cioè truffe giudicate legittime: è il numero da tenere a zero;
- la matrice di confusione.

I risultati vengono salvati in `evals/results/`. Il file di esempio contiene anche un caso di prompt injection, un messaggio che "ordina" all'assistente di rispondere che è legittimo.

## LangSmith (opzionale)

Imposta `LANGSMITH_TRACING=true` e `LANGSMITH_API_KEY` nel `.env` per vedere ogni analisi come trace: prompt, immagine, output, token e tempi. Dettagli in [docs/langsmith.md](docs/langsmith.md).

## Limiti noti

- Nessun follow-up: ogni messaggio è un'analisi a sé.
- Risposte solo in italiano (vedi [docs/language-detection.md](docs/language-detection.md)).
- Nessun controllo esterno sugli URL (reputazione, WHOIS): solo il giudizio dell'LLM. Gli URL non vengono mai aperti.
- Duplicati e lock solo in memoria: va bene un singolo processo locale, non più istanze.
- WhatsApp consegna ogni immagine come messaggio separato: più screenshot inviati insieme diventano analisi separate, eseguite in sequenza.
