# LangSmith: cosa fare e perché

LangSmith è la piattaforma di LangChain per osservare le applicazioni LLM. Per ogni analisi registra un **trace**: l'albero dei passi del grafo LangGraph, con input, output, durata e token di ogni chiamata a Claude.

I log strutturati ti dicono **cosa** è successo (evento, durata, verdetto). LangSmith ti fa vedere **perché**: il prompt esatto, l'immagine inviata, la risposta del modello. Usali insieme: l'`analysis_id` dei log si ritrova nei metadati del trace.

## 1. Setup (10 minuti, una volta sola)

1. Registrati su https://smith.langchain.com (c'è un piano gratuito per sviluppatori con un numero limitato di trace al mese; controlla i limiti attuali sulla pagina dei prezzi).
   - **Regione:** puoi scegliere l'istanza EU (https://eu.smith.langchain.com) per tenere i dati in Europa. Se la scegli, devi impostare anche `LANGSMITH_ENDPOINT` (vedi sotto).
2. **Settings → API Keys → Create API Key**. Copia la chiave: viene mostrata una volta sola.
3. Nel `.env`:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=whatsapp-phishing-agent
# solo se usi l'istanza EU:
# LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
```

4. Riavvia il server e fai un'analisi (con `/debug/analyze` o da WhatsApp).
5. Su smith.langchain.com apri il progetto `whatsapp-phishing-agent`: trovi un trace chiamato `phishing-analysis`.

Non serve scrivere codice: LangGraph invia i trace da solo quando `LANGSMITH_TRACING=true`. Il piano prevede solo due aggiunte:
- le chiamate a Claude sono decorate con `@traceable(run_type="llm")`, così compaiono come nodi LLM con input, output e token;
- il runner passa al grafo `run_name`, `tags` e `metadata` (`analysis_id`, `message_sid`, nome utente dalla whitelist). Non passa il numero di telefono.

## 2. Cosa guardare in un trace

```
phishing-analysis              (analysis_id=a1b2c3, 7.8s)
├── extract                    2.9s
│   └── claude.extract  (llm)  input: prompt + immagine · output: Extraction · token
├── judge                      4.6s
│   └── claude.judge    (llm)  input: Extraction · output: Verdict · token
└── format                     1ms
```

- **Verdetto sbagliato?** Apri `extract`: se il testo trascritto o gli URL sono sbagliati, il problema è nella lettura dell'immagine. Se l'estrazione è corretta, il problema è nel prompt di `judge`.
- **Lento?** Le durate dei nodi ti dicono dove si perde tempo.
- **Costo?** I token di input e output sono su ogni nodo LLM; il progetto mostra anche totali e costi stimati.

## 3. Collegare log e trace

Ogni riga di log ha `analysis_id`. In LangSmith: **filtro → Metadata → `analysis_id` = `a1b2c3`**. Così da un `analysis.failed` nei log arrivi in un clic al trace con prompt e risposta.

## 4. Privacy

Con il tracing attivo, **screenshot e testi finiscono su LangSmith**: probabilmente contengono numeri, nomi o dati bancari degli utenti.

- Per la PoC con screenshot di test: va bene.
- Con dati reali: aggiungi `LANGSMITH_HIDE_INPUTS=true` (e se serve `LANGSMITH_HIDE_OUTPUTS=true`). Vedrai ancora struttura, durate e token, ma non il contenuto.
- Per spegnere tutto: `LANGSMITH_TRACING=false`.

## 5. Usi utili durante la PoC (facoltativi)

1. **Feedback manuale:** su ogni trace puoi annotare se il verdetto era corretto. Dopo 20-30 analisi sai dove sbaglia l'agente.
2. **Dataset dai trace:** con "Add to Dataset" su un trace interessante (es. un falso negativo) costruisci un set di casi reali. Può affiancare o sostituire `evals/labels.json`.
3. **Playground:** apri una chiamata LLM, modifica il prompt e rilanciala sullo stesso input senza toccare il codice. Quando il prompt funziona, lo copi in `app/agent/prompts.py`.

## 6. Checklist

- [ ] Account creato (regione scelta: US o EU)
- [ ] API key in `.env`, `LANGSMITH_TRACING=true`
- [ ] Prima analisi visibile nel progetto `whatsapp-phishing-agent`
- [ ] Trovato un trace partendo dall'`analysis_id` di un log
- [ ] Decisione presa su `LANGSMITH_HIDE_INPUTS` prima di usare screenshot reali
