from typing import TypedDict

from app.agent.schemas import Extraction, ImageInput, Verdict


class AnalysisState(TypedDict, total=False):
    analysis_id: str
    caption: str | None
    images: list[ImageInput]
    extraction: Extraction
    verdict: Verdict
    reply_text: str
