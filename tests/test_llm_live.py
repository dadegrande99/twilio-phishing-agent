"""Real calls to the Anthropic API. Run with: uv run pytest -m live"""

import os

import pytest
from dotenv import load_dotenv

from app.agent.llm import create_llm
from app.config import Settings

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


async def test_live_text_phishing():
    llm = create_llm(Settings())
    extraction = await llm.extract(
        "Poste Italiane: il tuo conto è sospeso. Verifica entro 24h su "
        "http://poste-it-verifica.top/login",
        [],
    )
    assert any("poste-it-verifica.top" in url for url in extraction.urls)
    verdict = await llm.judge(extraction, None)
    assert verdict.label in {"phishing", "sospetto"}
