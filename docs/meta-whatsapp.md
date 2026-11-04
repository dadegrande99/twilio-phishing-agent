# WhatsApp Cloud API (Meta): come funziona (guida per questo progetto)

È l'alternativa a Twilio: il server parla direttamente con l'API di WhatsApp di Meta. Si attiva con `CHANNEL=meta` nel `.env`. Per la PoC è la strada più comoda: il **numero di test di Meta è gratuito** e permette risposte libere e accesso alle immagini, mentre il Trial di Twilio li blocca (vedi `docs/twilio.md` §12).

## 1. Il flusso

```
           ┌────────────┐   webhook POST (JSON)    ┌──────────────────┐
 Utente ──▶│  WhatsApp  │─────────────────────────▶│ FastAPI          │
           │   (Meta)   │                          │ /webhooks/meta   │
 Utente ◀──│            │◀─────────────────────────│                  │
           └────────────┘   Graph API POST /messages└──────────────────┘
```

A differenza di Twilio non c'è un intermediario: in entrata Meta chiama il nostro webhook, in uscita chiamiamo la Graph API `https://graph.facebook.com/v26.0/...`.

## 2. Glossario

| Termine | Significato |
|---|---|
| **App** | L'app creata su developers.facebook.com con il caso d'uso WhatsApp. Ha un **App Secret** |
| **WABA** | WhatsApp Business Account: contiene i numeri di telefono |
| **Phone number ID** | Id numerico del numero da cui inviamo (non è il numero di telefono). Va in `META_PHONE_NUMBER_ID` |
| **Numero di test** | Numero gratuito fornito da Meta. Può scrivere al massimo a **5 destinatari** verificati |
| **Access token** | Il token temporaneo dell'API Setup scade presto. Per la PoC usa un **System User token** (§9). Va in `META_ACCESS_TOKEN` |
| **App Secret** | Serve a verificare la firma dei webhook. Va in `META_APP_SECRET` |
| **Verify token** | Una stringa che scegli tu. Meta la rimanda quando registri il webhook, per dimostrare che l'URL è tuo. Va in `META_VERIFY_TOKEN` |
| **wamid** | Id di un messaggio WhatsApp (`wamid.HBg...`). Lo usiamo per il dedupe e per le spunte di lettura |
| **Finestra di 24 ore** | Puoi scrivere liberamente solo entro 24 ore dall'ultimo messaggio dell'utente. Il nostro bot risponde sempre entro pochi secondi |

## 3. Registrazione del webhook (handshake)

Quando salvi l'URL del webhook nell'App Dashboard, Meta fa una `GET`:

```
GET /webhooks/meta?hub.mode=subscribe&hub.verify_token=<il tuo verify token>&hub.challenge=1158201444
```

Se `hub.verify_token` coincide con `META_VERIFY_TOKEN`, il server risponde `200` con il valore di `hub.challenge` come testo semplice. Altrimenti risponde 403 e nei log compare `meta.verify_failed`.

## 4. Cosa arriva al server (webhook in entrata)

Una `POST` JSON. Ecco un messaggio con immagine (campi ridotti):

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<WABA_ID>",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "1555...", "phone_number_id": "<PHONE_NUMBER_ID>"},
        "contacts": [{"wa_id": "393331234567", "profile": {"name": "Davide"}}],
        "messages": [{
          "from": "393331234567",
          "id": "wamid.HBgM...",
          "timestamp": "1761310000",
          "type": "image",
          "image": {"id": "<MEDIA_ID>", "mime_type": "image/jpeg", "sha256": "...", "caption": "è vero?"}
        }]
      }
    }]
  }]
}
```

Cose da sapere:
- **`from` è senza `+`**: il codice lo normalizza in `+393331234567` prima di confrontarlo con la whitelist.
- **Un payload può contenere più messaggi**, e anche solo `statuses` (gli aggiornamenti sui nostri invii). Li elaboriamo tutti.
- **Tipi**:
  - `text` e `image` vengono analizzati; la didascalia dell'immagine è in `image.caption`.
  - `audio`, `video`, `document`, `sticker` e gli altri tipi ricevono "posso analizzare solo immagini o testo" (anche gli sticker, che pure sono immagini webp).
  - `reaction` (un'emoji sul nostro verdetto) viene ignorato.
- **Rispondiamo 200 subito a ogni payload con firma valida** (403 se la firma non è valida, 400 se il body non è JSON). Se non riceve 200, Meta ripete l'invio. Gli elementi malformati vengono saltati (`meta.payload.malformed`) senza perdere gli altri. Per questo l'analisi parte in background, e il dedupe sul `wamid` evita di analizzare due volte lo stesso messaggio.

## 5. Sicurezza: la firma `X-Hub-Signature-256`

Ogni `POST` di Meta ha l'header:

```
X-Hub-Signature-256: sha256=<HMAC-SHA256 del body grezzo, con chiave l'App Secret>
```

Il server ricalcola l'HMAC sul body così com'è arrivato, prima di interpretarlo come JSON, e lo confronta con un confronto a tempo costante. Se non coincide risponde 403 e logga `webhook.signature_invalid channel=meta`.

L'URL non entra nel calcolo, quindi qui non c'è la trappola di ngrok che c'è con Twilio. Se la firma fallisce, quasi sempre `META_APP_SECRET` è sbagliato, oppure appartiene a un'altra app.

## 6. Scaricare l'immagine (due passi)

1. `GET https://graph.facebook.com/v26.0/<MEDIA_ID>?phone_number_id=<PHONE_NUMBER_ID>` con `Authorization: Bearer <token>`. Restituisce `url`, `mime_type` e `file_size`.
   - Se `file_size` supera 3,75 MB rispondiamo subito "immagine troppo grande", senza scaricarla.
2. `GET <url>` con lo stesso header. L'URL vale circa 5 minuti.

Il token viene inviato solo via https a domini Meta (`facebook.com`, `fbsbx.com`, `fbcdn.net`, `whatsapp.net`). Un URL diverso viene rifiutato e loggato come `not_a_meta_url`.

## 7. Rispondere

```
POST https://graph.facebook.com/v26.0/<PHONE_NUMBER_ID>/messages
Authorization: Bearer <token>
{"messaging_product":"whatsapp","recipient_type":"individual","to":"393331234567",
 "type":"text","text":{"preview_url":false,"body":"🔴 PHISHING — ..."}}
```

**Ack nativo:** appena arriva un messaggio, al posto del testo "📥 Ricevuto" mandiamo:

```json
{"messaging_product":"whatsapp","status":"read","message_id":"<wamid>","typing_indicator":{"type":"text"}}
```

L'utente vede le spunte blu e "sta scrivendo…". L'indicatore sparisce quando arriva il nostro messaggio successivo, o al massimo dopo 25 secondi.

## 8. Stati dei messaggi inviati

Arrivano sullo stesso webhook, in `value.statuses[]`. Nei log li trovi come `meta.status.updated`:

```
sent ──▶ delivered ──▶ read
   └──▶ failed (error_code + error_title)
```

## 9. Setup passo per passo

L'interfaccia di Meta cambia spesso: i nomi delle voci possono essere leggermente diversi.

1. **Crea l'app.** Su https://developers.facebook.com → **My Apps → Create App**:
   - caso d'uso **"Connect with customers through WhatsApp"**;
   - scegli o crea un business portfolio.
2. **API Setup**, in **WhatsApp → API Setup**:
   - annota il **Phone number ID** del numero di test: va in `META_PHONE_NUMBER_ID`;
   - in **To** aggiungi il tuo numero e verificalo con il codice che ricevi su WhatsApp;
   - invia il template `hello_world`: deve arrivarti su WhatsApp;
   - **rispondi a quel messaggio dal telefono**: apre la finestra di 24 ore.
3. **App Secret**: in **App settings → Basic → App secret → Show**. Va in `META_APP_SECRET`.
4. **Token permanente.** Su https://business.facebook.com → **Settings → Users → System users**:
   - **Add**, ruolo Admin;
   - **Assign assets**: l'app (controllo completo) e il WhatsApp account;
   - **Generate token**: seleziona l'app e i permessi `whatsapp_business_messaging` e `whatsapp_business_management`;
   - il token va in `META_ACCESS_TOKEN`.
5. **Verify token**: inventa una stringa lunga e casuale e mettila in `META_VERIFY_TOKEN`.
6. **Avvia** server e ngrok, con `CHANNEL=meta` nel `.env`:

   ```bash
   uv run uvicorn app.main:create_app --factory --port 8000
   ngrok http 8000
   ```
7. **Webhook**: in **WhatsApp → Configuration → Webhook → Edit**:
   - **Callback URL**: `https://<il tuo dominio ngrok>/webhooks/meta`;
   - **Verify token**: lo stesso di `META_VERIFY_TOKEN`;
   - **Verify and save**: nei log compare `meta.verified`.
8. **Iscrizione**: in **Webhook fields**, attiva **Subscribe** sul campo **`messages`**. Senza questo passo non arriva nulla.
9. **Prova**: scrivi `ciao` al numero di test, poi manda uno screenshot.

## 10. Errori comuni

| Sintomo nei log | Causa | Cosa fare |
|---|---|---|
| Nessun `inbound.received` | Campo `messages` non sottoscritto, URL sbagliato o ngrok spento | §9 passi 7-8; controlla `http://127.0.0.1:4040` |
| `meta.verify_failed` | Il verify token in Dashboard non coincide con `META_VERIFY_TOKEN` | Allinea i due valori |
| `webhook.signature_invalid channel=meta` | `META_APP_SECRET` sbagliato o di un'altra app | Ricopia l'App Secret |
| `meta.message.failed error_code=190` | Token scaduto o non valido (quello temporaneo dura poco) | Usa il System User token |
| `meta.message.failed error_code=131030` | Destinatario non tra i 5 numeri verificati del numero di test | Aggiungilo in API Setup |
| `meta.message.failed error_code=131047` | Fuori dalla finestra di 24 ore | L'utente deve scrivere di nuovo |
| `inbound.not_whitelisted` | Numero non in `config/whitelist.json` | Aggiungilo in formato `+39…` |

## 11. Dove trovi tutto questo nel codice

| Concetto | File |
|---|---|
| Firma e parsing del payload | `app/channels/meta_security.py` |
| Invio, ack con spunte, download media | `app/channels/meta_client.py` |
| Endpoint GET/POST `/webhooks/meta` | `app/api/meta_webhook.py` |
| Scelta del canale | `app/main.py` (`create_gateway`), variabile `CHANNEL` |
| Orchestrazione: ack, avanzamento, verdetto | `app/agent/runner.py` (identico per i due canali) |
