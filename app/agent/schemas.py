"""Data exchanged between the graph nodes and the LLM (structured outputs)."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["sms", "email", "whatsapp", "social", "telefonata", "altro", "sconosciuto"]
Label = Literal["phishing", "sospetto", "probabilmente_legittimo", "indeterminato"]
Confidence = Literal["bassa", "media", "alta"]


class Extraction(BaseModel):
    """What the model reads from the screenshot or the forwarded text."""

    content_kind: Literal["messaggio_da_analizzare", "conversazione"] = Field(
        description=(
            "'conversazione' solo se l'utente saluta o chiede del servizio senza alcun "
            "messaggio da valutare; altrimenti 'messaggio_da_analizzare'."
        )
    )
    channel: Channel = Field(description="Canale da cui sembra arrivare il messaggio.")
    sender: str | None = Field(description="Mittente visibile (numero, email, nome), se c'è.")
    claimed_brand: str | None = Field(
        description="Ente o azienda che il messaggio dichiara di essere, se c'è."
    )
    text: str = Field(description="Testo del messaggio trascritto fedelmente.")
    urls: list[str] = Field(description="URL esattamente come appaiono, senza correggerli.")
    phone_numbers: list[str] = Field(description="Numeri di telefono presenti nel messaggio.")
    call_to_action: str | None = Field(description="Cosa chiede di fare il messaggio.")
    summary: str = Field(description="Una frase che riassume il messaggio.")


class Verdict(BaseModel):
    """The phishing assessment."""

    label: Label
    confidence: Confidence
    red_flags: list[str] = Field(description="Segnali di truffa, frasi brevi.")
    legit_signals: list[str] = Field(description="Segnali di legittimità, frasi brevi.")
    recommended_actions: list[str] = Field(description="Cosa deve fare l'utente, frasi brevi.")
    explanation: str = Field(description="Spiegazione in 2-3 frasi semplici.")


@dataclass(frozen=True)
class ImageInput:
    mime: str
    b64: str
