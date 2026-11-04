# Addendum: canale Meta, modello Haiku, un po' di conversazione

> **Storico.** Questo documento è un'istantanea del momento in cui è stato scritto (fa parte del flusso di lavoro con l'agente). Nomi di eventi, default e dettagli possono essere cambiati in seguito: la fonte di verità è il codice, e la documentazione corrente è nel README e in `docs/*.md`.

Data: 2026-10-24
Estende: `2026-10-24-whatsapp-phishing-agent-design.md`. Tutto ciò che qui non è detto resta come nello spec principale.

## 1. Perché

- **Account Trial Twilio:** blocca i messaggi di testo libero (errore 21654) e l'accesso alle immagini ("This feature is not available on a Trial account"). Così il bot non può funzionare senza upgrade a pagamento.
- **WhatsApp Cloud API di Meta:** offre un numero di test gratuito, con risposte libere entro 24 ore e accesso ai media. Elimina anche la commissione Twilio.
- **Haiku 5.5:** per una PoC basta e costa circa 20 volte meno di Sonnet 5.5.
- **Conversazione:** l'help fisso è rigido. Una risposta breve generata dall'LLM, limitata al servizio, rende il bot più naturale.

## 2. Obiettivi

- Due canali completi e intercambiabili, **Twilio** e **Meta Cloud API**, scelti con la variabile d'ambiente `CHANNEL=twilio|meta`. Runner, grafo, whitelist, log ed eval restano condivisi.
- Modello di default `claude-haiku-5-5`, sempre configurabile con `ANTHROPIC_MODEL`.
- Su Meta, il messaggio "📥 Ricevuto" è sostituito da spunte blu e indicatore "sta scrivendo…". Su Twilio resta il messaggio di testo.
- **(Fase finale, dopo il test end-to-end)** Ai messaggi "conversazione" risponde l'LLM in modo breve e solo sul servizio, senza memoria. Se la chiamata fallisce, si usa l'help fisso.

## 3. Non-obiettivi

- Memoria e follow-up multi-turno.
- Template WhatsApp e messaggi fuori dalla finestra di 24 ore.
- Più canali attivi contemporaneamente: ne è attivo uno per processo.

## 4. Vincoli e regole invariate

Restano tutti i vincoli dello spec principale: whitelist fail-closed, nessuna risposta ai numeri non autorizzati, nessun URL aperto, niente contenuti su disco, numeri mascherati nei log, nessun `temperature` o `tool_choice`, webhook che risponde prima di qualsiasi lavoro.

In più:
- Il token Meta viene inviato solo a host Meta, via https.
- Il fallback server-side (`fallbacks`) viene inviato solo ai modelli che lo supportano. Haiku 5.5 non lo supporta.

## 5. Canale Meta: comportamento

| Aspetto | Comportamento |
|---|---|
| Verifica webhook | `GET /webhooks/meta`: se `hub.mode=subscribe` e `hub.verify_token == META_VERIFY_TOKEN`, risponde 200 con `hub.challenge` in text/plain; altrimenti 403 |
| Firma | `POST /webhooks/meta` verifica `X-Hub-Signature-256 = "sha256=" + HMAC-SHA256(app_secret, body grezzo)` con confronto a tempo costante; se non valida, 403. Disattivabile con `VALIDATE_META_SIGNATURE=false` solo in locale |
| Risposta | Sempre 200 subito per i payload validi, anche per numeri non autorizzati o eventi ignorati: Meta ripete gli invii che non ricevono 200 |
| Parsing | `entry[].changes[].value`: `messages[]` diventano `InboundMessage` (anche più di uno per payload); `statuses[]` vengono loggati |
| Numero | `from` arriva senza `+`: viene normalizzato in `+` più le cifre. Se non è numerico, il messaggio è trattato come non autorizzato |
| Tipi | `text` → `body`; `image` → media (`id`, `mime_type`) e `body` = didascalia; `audio`/`video`/`document`/`sticker` → media con il loro mime, quindi rifiutati dal runner come oggi; `reaction` → ignorato (la reazione con emoji al verdetto non deve generare risposte); altri tipi → media `unsupported/<tipo>`, cioè messaggio "solo immagini o testo" |
| Download | `GET /{version}/{media_id}` restituisce `url`, `mime_type` e `file_size`. Se `file_size` supera il limite, risposta `too_large` senza scaricare. Poi `GET url` con Bearer, solo se l'URL è https su un host Meta |
| Invio | `POST /{version}/{phone_number_id}/messages` con il JSON di tipo `text` |
| Ack | `POST …/messages` con `{"status":"read","message_id":…,"typing_indicator":{"type":"text"}}` |
| Stati | Log `meta.status.updated` con id, status, destinatario mascherato e codice/titolo dell'eventuale errore |

Variabili: `CHANNEL`, `META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_GRAPH_VERSION=v26.0`, `VALIDATE_META_SIGNATURE=true`. Le credenziali obbligatorie sono solo quelle del canale scelto.

## 6. Conversazione (fase finale)

- Nodo `chat` al posto di `help` per `content_kind == "conversazione"` (mai quando ci sono immagini).
- `llm.chat(text) -> str`: system prompt limitato a cosa fa il servizio, come usarlo e cosa fare se si è già cliccato un link. Niente altri argomenti. Massimo circa 600 caratteri, effort `low`, italiano.
- Qualsiasi errore o rifiuto porta all'help fisso `HELP_TEXT`.

## 7. Scelte fatte da Claude (da verificare)

- Ack nativo su Meta, cioè spunte blu più "sta scrivendo…", al posto del testo "Ricevuto".
- Reazioni con emoji ignorate.
- `MediaRef.url` rinominato in `MediaRef.locator`: per Twilio è l'URL, per Meta il media id.
- Default `CHANNEL=twilio` nel codice, per compatibilità; nel tuo `.env` lo impostiamo a `meta`.
