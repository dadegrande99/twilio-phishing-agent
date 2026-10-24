"""Scoring of eval runs, oriented to user safety.

`sospetto` counts as correct when `phishing` is expected (the user takes the same
action). A "dangerous miss" is a scam labelled `probabilmente_legittimo`.
"""

from dataclasses import dataclass, field

_SAFE_EQUIVALENTS = {"phishing": {"phishing", "sospetto"}}
_SCAM_LABELS = {"phishing", "sospetto"}


@dataclass
class SampleResult:
    sample_id: str
    expected: str
    predicted: str | None
    error: str | None = None


@dataclass
class EvalReport:
    total: int
    correct: int
    accuracy: float
    dangerous_misses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)


def is_correct(expected: str, predicted: str | None) -> bool:
    return predicted is not None and predicted in _SAFE_EQUIVALENTS.get(expected, {expected})


def score(results: list[SampleResult]) -> EvalReport:
    confusion: dict[str, dict[str, int]] = {}
    correct = 0
    for result in results:
        predicted = result.predicted or "errore"
        row = confusion.setdefault(result.expected, {})
        row[predicted] = row.get(predicted, 0) + 1
        correct += is_correct(result.expected, result.predicted)
    return EvalReport(
        total=len(results),
        correct=correct,
        accuracy=correct / len(results) if results else 0.0,
        dangerous_misses=[
            r.sample_id
            for r in results
            if r.expected in _SCAM_LABELS and r.predicted == "probabilmente_legittimo"
        ],
        errors=[r.sample_id for r in results if r.error],
        confusion=confusion,
    )
