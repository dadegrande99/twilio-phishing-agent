from app.agent.formatting import (
    FOOTER_TEXT,
    MAX_BULLETS,
    format_progress,
    format_verdict,
)
from tests.fakes import make_extraction, make_verdict


def test_phishing_header_and_red_flags():
    text = format_verdict(
        make_verdict(label="phishing", confidence="alta", red_flags=["Dominio falso"])
    )
    assert text.startswith("🔴 PHISHING — confidenza alta")
    assert "Perché:\n• Dominio falso" in text
    assert text.endswith(FOOTER_TEXT)


def test_legit_uses_legit_signals():
    text = format_verdict(
        make_verdict(
            label="probabilmente_legittimo", legit_signals=["Dominio ufficiale"], red_flags=["x"]
        )
    )
    assert "• Dominio ufficiale" in text and "• x" not in text


def test_indeterminato_has_no_perche():
    assert "Perché:" not in format_verdict(make_verdict(label="indeterminato"))


def test_empty_sections_omitted():
    text = format_verdict(make_verdict(label="phishing", red_flags=[], recommended_actions=[]))
    assert "Perché:" not in text and "Cosa fare:" not in text


def test_long_verdict_fits_whatsapp():
    v = make_verdict(
        red_flags=["a" * 500] * 20, recommended_actions=["b" * 500] * 20, explanation="c" * 2000
    )
    text = format_verdict(v)
    assert len(text) <= 1500
    assert text.count("• ") == 2 * MAX_BULLETS


def test_progress_with_brand():
    e = make_extraction(channel="sms", claimed_brand="Poste Italiane")
    assert format_progress(e) == (
        '🔎 Sembra un SMS che si presenta come "Poste Italiane". '
        "Controllo link, mittente e contenuto…"
    )


def test_progress_without_brand():
    assert format_progress(make_extraction(channel="email")) == (
        "🔎 Sembra un'email. Controllo link, mittente e contenuto…"
    )


def test_progress_unknown_channel():
    assert format_progress(make_extraction(channel="sconosciuto")).startswith("🔎 Ho letto")
