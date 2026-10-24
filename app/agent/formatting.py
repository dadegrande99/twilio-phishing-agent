"""User-facing WhatsApp texts (Italian)."""

from app.agent.schemas import Extraction, Verdict

ACK_TEXT = "📥 Ricevuto! Analizzo il messaggio, ci vorrà qualche secondo…"
HELP_TEXT = (
    "👋 Inviami lo screenshot (o il testo) di un messaggio sospetto: "
    "SMS, email, WhatsApp o social. Ti dirò se sembra phishing e cosa fare."
)
UNSUPPORTED_MEDIA_TEXT = "⚠️ Posso analizzare solo immagini (screenshot) o testo."
TOO_LARGE_TEXT = "⚠️ L'immagine è troppo grande. Prova a inviare uno screenshot più leggero."
GENERIC_ERROR_TEXT = "⚠️ Non sono riuscito a completare l'analisi, riprova tra poco."
REFUSAL_TEXT = (
    "⚠️ Non posso analizzare questo contenuto in modo automatico. "
    "Nel dubbio non cliccare link e contatta l'ente tramite i canali ufficiali."
)
FOOTER_TEXT = "ℹ️ Analisi automatica: nel dubbio contatta l'ente dai suoi canali ufficiali."

LABEL_HEADERS = {
    "phishing": "🔴 PHISHING",
    "sospetto": "🟠 SOSPETTO",
    "probabilmente_legittimo": "🟢 PROBABILMENTE LEGITTIMO",
    "indeterminato": "⚪ NON DETERMINABILE",
}
CHANNEL_PHRASES = {
    "sms": "un SMS",
    "email": "un'email",
    "whatsapp": "un messaggio WhatsApp",
    "social": "un messaggio social",
    "telefonata": "una telefonata",
    "altro": "un messaggio",
}

MAX_MESSAGE_CHARS = 1500
MAX_BULLETS = 4
MAX_BULLET_CHARS = 120
MAX_EXPLANATION_CHARS = 300

_CHECKING = "Controllo link, mittente e contenuto…"


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"• {_truncate(item, MAX_BULLET_CHARS)}" for item in items[:MAX_BULLETS])


def format_progress(extraction: Extraction) -> str:
    phrase = CHANNEL_PHRASES.get(extraction.channel)
    if phrase is None:
        return f"🔎 Ho letto il messaggio. {_CHECKING}"
    if extraction.claimed_brand:
        return f'🔎 Sembra {phrase} che si presenta come "{extraction.claimed_brand}". {_CHECKING}'
    return f"🔎 Sembra {phrase}. {_CHECKING}"


def format_verdict(verdict: Verdict) -> str:
    sections = [
        f"{LABEL_HEADERS[verdict.label]} — confidenza {verdict.confidence}",
        _truncate(verdict.explanation, MAX_EXPLANATION_CHARS),
    ]

    if verdict.label in ("phishing", "sospetto"):
        reasons = verdict.red_flags
    elif verdict.label == "probabilmente_legittimo":
        reasons = verdict.legit_signals
    else:
        reasons = []

    lists = []
    if reasons:
        lists.append(f"Perché:\n{_bullets(reasons)}")
    if verdict.recommended_actions:
        lists.append(f"Cosa fare:\n{_bullets(verdict.recommended_actions)}")
    if lists:
        sections.append("\n".join(lists))

    sections.append(FOOTER_TEXT)
    text = "\n\n".join(s for s in sections if s)
    return _truncate(text, MAX_MESSAGE_CHARS)
