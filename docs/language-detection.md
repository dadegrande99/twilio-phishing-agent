# Come capire la lingua dell'utente

> **Proposta, non implementata.** Il campo `lang` nella whitelist, l'evento `language.resolved` e la libreria `lingua-language-detector` descritti qui non esistono nel codice: oggi le risposte sono sempre in italiano.

Oggi l'agente risponde sempre in italiano. Questo documento spiega come rispondere nella lingua dell'utente, quali segnali usare e in che ordine.

## Il punto chiave: lingua dell'utente ≠ lingua dello screenshot

Il contenuto dello screenshot non dice in che lingua parla l'utente. Un utente italiano può ricevere una truffa in inglese ("Your package is on hold"), e un utente inglese che vive in Italia può ricevere un finto SMS di Poste Italiane. Se l'agente rispondesse nella lingua dello screenshot, sbaglierebbe proprio nei casi più comuni.

La lingua dello screenshot resta comunque utile all'analisi: una truffa in inglese che si presenta come una banca italiana è un segnale d'allarme. Va estratta, ma come informazione sul messaggio sospetto, non sull'utente.

## Segnali disponibili

Dal più affidabile al meno affidabile:

1. **Preferenza esplicita.** Un campo `lang` nella whitelist (`{ "number": "+44...", "name": "John", "lang": "en" }`) oppure un comando dell'utente (es. "lingua: inglese"). È affidabile al 100% e costa zero, ma qualcuno deve impostarla.

2. **Testo scritto dall'utente.** Didascalia dello screenshot o messaggio di testo. Se l'utente scrive "è vero questo?" sotto l'immagine, la lingua è chiara. Le didascalie però sono spesso assenti o brevissime ("??", "ok?"), e sotto le 3-4 parole il rilevamento è inaffidabile.

3. **Prefisso internazionale del numero.** Con la libreria `phonenumbers`: `+39` dà Italia, quindi italiano. È sempre disponibile ma debole: expat, paesi multilingua (Svizzera `+41`, Belgio `+32`) e numeri esteri usati in Italia.

4. **Default.** Italiano.

## Come rilevare la lingua del testo

- **Dentro la chiamata LLM esistente (consigliato).** Si aggiungono al nodo `extract` due campi dello schema `Extraction`:
  - `user_language`: lingua della didascalia dell'utente (codice ISO 639-1), oppure `null` se non c'è didascalia o è troppo corta per deciderlo;
  - `content_language`: lingua del messaggio sospetto nello screenshot.

  Non serve nessuna chiamata in più, e il modello gestisce bene testi brevi e misti. Vale solo quando c'è un'immagine o un testo da analizzare, ma è proprio il caso d'uso principale.
- **Libreria locale** (`lingua-language-detector`): deterministica, offline, buona sui testi brevi. È utile se serve la lingua prima di chiamare l'LLM, per esempio per il messaggio "Ricevuto!", che parte prima dell'analisi.

## Regola di decisione proposta

```
lingua = preferenza_whitelist
      or user_language (dalla didascalia, se ≥ 3 parole o rilevata con confidenza)
      or lingua_da_prefisso(numero)
      or "it"
```

Note pratiche:
- **Il primo messaggio** ("Ricevuto!") parte prima dell'LLM, quindi può usare solo la whitelist o il prefisso. Dal messaggio di avanzamento in poi si può usare la lingua rilevata. Una piccola incoerenza al primo messaggio è accettabile; in alternativa si rileva la didascalia con `lingua-language-detector` prima dell'ack.
- **Memoria:** senza follow-up non c'è stato tra un'analisi e l'altra. Se in futuro si vuole ricordare la lingua, basta una piccola mappa numero → ultima lingua rilevata.
- **Lingue supportate:** conviene fissare un elenco chiuso (es. `it`, `en`, `es`, `fr`, `de`) e ricadere sull'italiano per le altre, così i messaggi fissi (ack, errori, etichette) restano tradotti a mano e controllati.

## Cosa cambia nel codice

1. Aggiungere `lang` opzionale al formato della whitelist.
2. Aggiungere `user_language` e `content_language` a `Extraction`.
3. Spostare i testi fissi (ack, errori, etichette, intestazioni "Perché:"/"Cosa fare:") in un dizionario per lingua.
4. Passare la lingua scelta al nodo `judge`, così `red_flags`, `explanation` e `recommended_actions` vengono generati direttamente in quella lingua.
5. Loggare `language.resolved` con la lingua scelta e la fonte (`whitelist` | `caption` | `prefix` | `default`), per capire quale segnale viene usato davvero.
