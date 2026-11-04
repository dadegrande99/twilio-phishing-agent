# Twilio + WhatsApp: come funziona (guida per questo progetto)

Questa guida spiega Twilio partendo da zero, con riferimento a cosa fa il nostro codice in ogni passaggio. Leggila prima del piano 2 (layer Twilio).

## 1. Cos'è Twilio in questo progetto

Twilio fa da intermediario fra WhatsApp e il nostro server. Noi non parliamo mai direttamente con WhatsApp/Meta:

- **In entrata**: quando l'utente scrive al numero WhatsApp, Meta consegna il messaggio a Twilio, e Twilio lo inoltra al nostro server con una chiamata HTTP (webhook).
- **In uscita**: quando vogliamo scrivere all'utente, chiamiamo l'API REST di Twilio, e Twilio consegna il messaggio su WhatsApp.

```
           ┌────────────┐   webhook (HTTP POST)    ┌─────────────────┐
 Utente ──▶│  WhatsApp  │──▶ Twilio ──────────────▶│ FastAPI (nostro)│
           │   (Meta)   │                          │                 │
 Utente ◀──│            │◀── Twilio ◀──────────────│ REST API Twilio │
           └────────────┘   messages.create()      └─────────────────┘
```

## 2. Glossario minimo

| Termine | Significato |
|---|---|
| **Account SID** (`AC...`) | Identificativo del tuo account Twilio. È come uno username, non è segreto. |
| **Auth Token** | La password dell'account. Serve per chiamare l'API, scaricare le immagini e verificare la firma dei webhook. È segreto: solo in `.env`, mai nei log. |
| **Sender** | Il numero WhatsApp da cui Twilio invia. Nella sandbox è condiviso: `whatsapp:+14155238886`. |
| **Sandbox** | Ambiente di prova gratuito. Ogni utente deve prima "unirsi" mandando `join <parola-chiave>` al numero sandbox. |
| **Webhook** | L'URL del nostro server che Twilio chiama a ogni messaggio in entrata. |
| **TwiML** | Il formato XML con cui si può rispondere a un webhook. Noi rispondiamo sempre con un TwiML vuoto (`<Response/>`). |
| **Message SID** (`SM...`/`MM...`) | Identificativo univoco di ogni messaggio. Lo usiamo per tracciare e deduplicare. |
| **Status callback** | Un secondo webhook con cui Twilio ci aggiorna sullo stato dei messaggi che inviamo (inviato, consegnato, letto, fallito). |
| **Finestra di 24 ore** | Regola di WhatsApp: puoi scrivere liberamente a un utente solo entro 24 ore dal suo ultimo messaggio. Fuori finestra servono template pre-approvati. Il nostro agente risponde sempre entro pochi secondi, quindi è sempre dentro la finestra. |

## 3. Cosa arriva al nostro server (webhook in entrata)

Twilio fa una `POST` con corpo `application/x-www-form-urlencoded` (come un form HTML, **non** JSON). Esempio per uno screenshot con didascalia:

```
MessageSid=MM5f3c...           # id univoco del messaggio
AccountSid=AC1234...           # il tuo account
From=whatsapp:+393331234567    # chi scrive (con prefisso "whatsapp:")
To=whatsapp:+14155238886       # il nostro sender
Body=È vero questo SMS?        # testo o didascalia (vuoto se solo immagine)
NumMedia=1                     # quanti allegati
MediaUrl0=https://api.twilio.com/2010-04-01/Accounts/AC.../Messages/MM.../Media/ME...
MediaContentType0=image/jpeg
ProfileName=Davide             # nome del profilo WhatsApp
WaId=393331234567              # numero WhatsApp senza "+"
```

Cose da sapere:
- **Un'immagine per messaggio.** Se l'utente seleziona tre screenshot e li invia insieme, WhatsApp li consegna come tre messaggi separati, quindi arrivano tre webhook. Per noi sono tre analisi, eseguite una dopo l'altra (lock per utente).
- **Didascalia = `Body`.** Se l'utente scrive del testo sotto l'immagine, arriva in `Body` insieme all'immagine.
- **Vocali, video, PDF** arrivano con lo stesso schema ma con un `MediaContentType` diverso (`audio/ogg`, `video/mp4`, `application/pdf`). Li rifiutiamo con un messaggio gentile.

## 4. Perché rispondiamo subito e lavoriamo dopo

Twilio aspetta la risposta al webhook per **15 secondi** al massimo, poi considera la chiamata fallita. Un'analisi con l'LLM può durare di più. Per questo:

1. il webhook fa solo controlli veloci (firma, whitelist, duplicati) e risponde subito `200` con TwiML vuoto;
2. l'analisi parte in background (`BackgroundTasks` di FastAPI, eseguito dopo l'invio della risposta);
3. tutti i messaggi all'utente (ricevuto, avanzamento, verdetto) partono con l'**API REST** (`client.messages.create`), non con il TwiML.

Il TwiML permetterebbe di rispondere con un solo messaggio dentro la risposta del webhook, ma non va bene per noi: non possiamo mandare aggiornamenti successivi e dobbiamo stare nei 15 secondi.

```
Twilio            FastAPI                         Background               Twilio REST
  │ POST webhook ──▶│ firma ✓ whitelist ✓ dedupe ✓   │                          │
  │◀── 200 <Response/>                              │                          │
  │                 │── avvia analisi ─────────────▶│── "📥 Ricevuto" ────────▶│──▶ utente
  │                 │                               │   scarica immagine        │
  │                 │                               │   LLM: extract            │
  │                 │                               │── "🔎 Sembra un SMS…" ──▶│──▶ utente
  │                 │                               │   LLM: judge              │
  │                 │                               │── "🔴 PHISHING…" ──────▶│──▶ utente
```

## 5. Sicurezza: la firma `X-Twilio-Signature`

L'URL del webhook è pubblico (ngrok), quindi chiunque potrebbe chiamarlo fingendo di essere Twilio. Per distinguere le chiamate vere, Twilio firma ogni richiesta:

- prende l'URL completo chiamato e aggiunge i parametri POST, ordinati per nome, nella forma `nomevalore`;
- calcola un HMAC-SHA1 di questa stringa usando l'Auth Token come chiave;
- mette il risultato (in base64) nell'header `X-Twilio-Signature`.

Noi rifacciamo lo stesso calcolo con `twilio.request_validator.RequestValidator`. Se non coincide, rispondiamo `403` e logghiamo `webhook.signature_invalid`.

**Trappola tipica con ngrok:** la firma è calcolata sull'URL pubblico (`https://abcd.ngrok-free.app/webhooks/twilio/whatsapp`), ma FastAPI vede `http://localhost:8000/...`. Per questo il codice ricostruisce l'URL con `PUBLIC_BASE_URL`. Se riavvii ngrok e l'URL cambia, devi aggiornare **sia** `PUBLIC_BASE_URL` nel `.env` **sia** l'URL nella console Twilio, altrimenti tutte le richieste ricevono 403. Il log `webhook.signature_invalid` stampa l'URL usato per la verifica, così vedi subito se non coincide.

## 6. Scaricare l'immagine

`MediaUrl0` non è un file pubblico: è una risorsa dell'API Twilio. La scarichiamo con `httpx`:
- autenticazione **basic** con `(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)`;
- `follow_redirects=True`, perché Twilio risponde con un redirect verso lo storage vero e proprio;
- controllo di tipo (`image/jpeg`, `image/png`, `image/webp`) e dimensione (massimo 3,75 MB: in base64 restano sotto i 5 MB per immagine accettati da Claude);
- download solo da `https://api.twilio.com`: le credenziali non vengono mai inviate ad altri host.

L'immagine resta solo in memoria (base64) per la durata dell'analisi.

## 7. Inviare messaggi e seguirne lo stato

```python
client.messages.create(
    from_="whatsapp:+14155238886",
    to="whatsapp:+393331234567",
    body="📥 Ricevuto! Analizzo il messaggio…",
    status_callback="https://abcd.ngrok-free.app/webhooks/twilio/status",
)
```

L'SDK `twilio` è sincrono, quindi lo chiamiamo con `asyncio.to_thread` per non bloccare il server. La chiamata ritorna subito con lo stato `queued`: "accettato da Twilio", non "consegnato".

Il codice passa `status_callback` **solo con `INBOUND_MODE=webhook`**: in polling non c'è un URL pubblico, quindi gli stati si vedono solo nella Console. Grazie a `status_callback`, Twilio ci richiama a ogni cambio di stato. Il nostro endpoint `/webhooks/twilio/status` logga `twilio.status.updated`, così nei log vedi il ciclo di vita completo:

```
queued ──▶ sending ──▶ sent ──▶ delivered ──▶ read
                         └──▶ undelivered / failed  (con ErrorCode)
```

Limite: 1600 caratteri per messaggio. Il nostro messaggio finale resta sotto i 1500.

## 8. Errori comuni

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| Tutti i webhook ricevono 403 | `PUBLIC_BASE_URL` diverso dall'URL configurato su Twilio (ngrok riavviato) | Allinea `.env` e console, riavvia il server |
| Nessun log `inbound.received` | URL webhook sbagliato in console, oppure ngrok spento | Controlla la console Twilio e l'interfaccia di ngrok (`http://127.0.0.1:4040`) |
| `inbound.not_whitelisted` per il tuo numero | Numero non in `config/whitelist.json`, o non in formato E.164 | Aggiungi `+39...` senza spazi |
| Errore **63015** (in `twilio.status.updated` in modalità webhook, oppure in Monitor → Logs) | Il destinatario non si è unito alla sandbox, o l'adesione è scaduta | Rimanda `join <parola>` al numero sandbox |
| Errore **63016** | Messaggio fuori dalla finestra di 24 ore | Non dovrebbe capitare: l'utente ha appena scritto |
| `media.rejected` con `reason=download_failed status=401` | Download con credenziali sbagliate | Controlla SID/token nel `.env` |

Per gli altri codici d'errore: Twilio Console → **Monitor → Logs → Errors**. Ogni codice ha una pagina con la spiegazione.

## 9. Setup della sandbox (passo per passo)

I passi 1-3 servono sempre. I passi 4-7 (ngrok e webhook) servono **solo con `INBOUND_MODE=webhook`**: in polling (default) basta avviare il server, vedi §13.

1. Crea un account su twilio.com, poi nella Console copia **Account SID** e **Auth Token** in `.env`.
2. Console → **Messaging → Try it out → Send a WhatsApp message**. Trovi il numero sandbox e la tua parola `join <xxx-yyy>`.
3. Dal tuo WhatsApp manda `join <xxx-yyy>` al numero sandbox. Ripeti per ogni tester. L'adesione scade dopo qualche giorno: se smetti di ricevere messaggi, rimandala.
4. Avvia il server: `uv run uvicorn app.main:create_app --factory --port 8000`.
5. Avvia ngrok: `ngrok http 8000` e copia l'URL `https://....ngrok-free.app`.
6. Metti quell'URL in `PUBLIC_BASE_URL` nel `.env` e riavvia il server.
7. Console → **Sandbox settings**:
   - *When a message comes in*: `https://....ngrok-free.app/webhooks/twilio/whatsapp`, metodo `POST`
   - *Status callback URL*: lascialo vuoto (lo passiamo noi in ogni `messages.create`)
8. Aggiungi il tuo numero a `config/whitelist.json`.
9. Manda uno screenshot al numero sandbox e guarda i log.

Strumenti utili per capire cosa succede:
- **Interfaccia di ngrok** `http://127.0.0.1:4040`: mostra ogni richiesta arrivata da Twilio con header e corpo, e permette di ripeterla ("Replay"), utile per il debug.
- **Twilio Console → Monitor → Logs → Messaging**: ogni messaggio in entrata e in uscita, con stato ed eventuale errore.

## 10. Passare al numero WhatsApp Business

Quando il sender Business è approvato su Twilio (Console → **Messaging → Senders → WhatsApp senders**):
1. imposta `TWILIO_WHATSAPP_FROM=whatsapp:+39XXXXXXXXXX`;
2. sul sender configura lo stesso webhook in entrata della sandbox;
3. il codice non cambia: niente più `join`, chiunque può scrivere al numero, ma la whitelist continua a filtrare.

Con il numero Business i messaggi sono a pagamento (tariffa Twilio + tariffa Meta per conversazione). Controlla la pagina prezzi di Twilio WhatsApp prima di aprirlo a più utenti.

## 11. Dove trovi tutto questo nel codice

| Concetto | File |
|---|---|
| Firma e parsing del webhook | `app/channels/twilio_security.py` |
| Invio messaggi e download immagini | `app/channels/twilio_client.py` |
| Endpoint webhook e status callback | `app/api/twilio_webhook.py` |
| Orchestrazione: ack, avanzamento, verdetto | `app/agent/runner.py` |

## 12. Account Trial e nuova "Tryout UI"

Gli account Twilio nuovi partono in **Trial** con una pagina "Try out WhatsApp" diversa dalla sandbox classica:
- **Mittente diverso:** nel nostro test era un numero tedesco (+49…) mostrato nella pagina "Try out WhatsApp", non +1 415 523 8886. Impostalo in `TWILIO_WHATSAPP_FROM`. Il codice di adesione era `join twilio-trial`. Dopo l'upgrade quel numero non era più collegato all'account: si è passati alla sandbox classica.
- **Webhook:** si configura nella scheda **Receive a message → Custom → Webhook URL** (metodo `HTTP POST`).
- **Limiti del Trial**, verificati sul nostro account:
  - invio di testo libero via API → errore **21654 "ContentSid Required"**: in Trial si possono mandare solo i template forniti da Twilio;
  - accesso ai media → **401 "This feature is not available on a Trial account"**.

Quindi in Trial il bot riceve i messaggi ma non riesce né a leggere gli screenshot né a rispondere. Servono l'**upgrade** dell'account oppure il canale Meta (`CHANNEL=meta`, vedi `docs/meta-whatsapp.md`), che con il numero di test funziona gratis.

## 13. Ricezione via polling (senza webhook)

Con `INBOUND_MODE=polling`, il default, il server non espone nessun webhook: è lui a chiedere a Twilio, ogni `POLL_INTERVAL_SECONDS` secondi, i messaggi arrivati.

```
loop ogni 5 s:
  GET /2010-04-01/Accounts/{SID}/Messages.json?To=whatsapp:+<nostro mittente>&DateSent>=<ultimo visto - 60 s>
  → per ogni messaggio inbound mai visto (in ordine cronologico):
       se ha media: GET …/Messages/{MessageSid}/Media.json → URL del media
       stesso percorso del webhook: whitelist → dedupe → analisi → risposta via REST
```

Perché funziona: Twilio salva sempre i messaggi in arrivo nell'account, anche se il webhook non è configurato o non risponde. Puoi lasciare vuoto il campo *When a message comes in* della sandbox. Se resta un vecchio URL, Twilio registra un errore nel Debugger, ma il messaggio viene salvato comunque.

**Pro:**
- niente ngrok e niente URL pubblico;
- niente firma da verificare;
- funziona dietro qualsiasi rete.

**Contro:**
- fino a `POLL_INTERVAL_SECONDS` secondi di ritardo in più;
- una chiamata API a ogni giro;
- senza URL pubblico non c'è `status_callback`: gli stati (delivered, read, failed) si vedono in **Monitor → Logs → Messaging**.

**Comportamento:**
- **All'avvio** si parte da "adesso": i messaggi vecchi non vengono rianalizzati.
- **Errori di rete o DNS:** ogni errore viene loggato come `poller.error` e il giro successivo riprova. Il poller non si ferma mai. Il DNS del router che a volte non risolve `api.twilio.com` produce proprio questo errore; se succede spesso, usa DNS `1.1.1.1` / `8.8.8.8`.
- **Trial:** il polling cambia solo il modo di ricevere i messaggi. I limiti del Trial (§12) restano: niente testo libero e niente media senza upgrade.
- **Solo Twilio:** con `CHANNEL=meta` serve `INBOUND_MODE=webhook`.

Nel codice: `app/channels/twilio_poller.py` (poller) e `app/main.py` (avvio e arresto insieme all'app).

## 14. Account a pagamento: KYC e costi

Cosa abbiamo incontrato dopo l'upgrade dell'account (ottobre 2026):

1. **Sandbox classica.** Dopo l'upgrade la pagina "Try WhatsApp" mostra il numero condiviso **+1 415 523 8886** e un codice `join <parola>`. Il primo `join` può fallire con *"Failed to join sandbox"*. Nel nostro caso è andato a buon fine dopo aver configurato l'Inbound URL della sandbox (*When a message comes in*). Se il campo resta vuoto, Twilio risponde a ogni messaggio con *"You said: … Configure your WhatsApp Sandbox's Inbound URL…"*; in polling basta che contenga un URL qualsiasi.
2. **KYC: errore `20003`, "Primary compliance profile is not approved".** Un account a pagamento può scrivere liberamente solo dopo che Twilio ha approvato un **Primary Compliance Profile** (Trust Hub). Prima di allora può scrivere solo ai numeri verificati (*Verified Caller IDs*).
   - Per una persona fisica: Trust Hub → *Create Primary Customer Profile* → **Individual**. Servono nome come sul documento, data di nascita, indirizzo, foto del documento e un selfie dalla Console.
   - Revisione: fino a 48 ore (nel nostro caso molto meno). Stati: *In Review* → *Approved* / *Rejected*. Se viene rifiutato, si corregge e si reinvia.
3. **Costi osservati.** Circa **0,03 $ per messaggio inviato** a un numero italiano: commissione Twilio 0,005 $ più la tariffa per messaggio di Meta. I messaggi in arrivo dalla sandbox risultavano a 0 $. Un'analisi completa (3 messaggi) costa quindi circa 0,09 $. Ipotesi, non documentata: la sandbox è un numero condiviso, quindi la quota mensile gratuita di service messages di Meta non si applica in pratica. Con un mittente proprio i primi 1.000 service messages al mese dovrebbero pagare solo la commissione Twilio.

Fonti:
- [Twilio – WhatsApp pricing](https://www.twilio.com/en-us/whatsapp/pricing)
- [Twilio – Primary compliance profiles](https://www.twilio.com/docs/trust-hub/profiles/primary-compliance-profiles)
- [Twilio – errore 20003](https://www.twilio.com/docs/api/errors/20003)
- [Twilio – errore 21608](https://www.twilio.com/docs/api/errors/21608)
- [Meta – WhatsApp pricing](https://developers.facebook.com/docs/whatsapp/pricing)
