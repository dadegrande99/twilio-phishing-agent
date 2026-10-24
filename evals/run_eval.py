"""Run the real analysis graph on labelled samples and score the results.

Usage:
    uv run python -m evals.run_eval [--labels evals/labels.json] [--yes]

Every sample costs two Claude calls.
"""

import argparse
import asyncio
import json
import mimetypes
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from app.agent.graph import build_graph, run_analysis
from app.agent.llm import create_llm
from app.config import Settings
from app.logging import configure_logging
from app.media import validate_image
from evals.scoring import SampleResult, is_correct, score

EVALS_DIR = Path(__file__).parent
SAMPLES_DIR = EVALS_DIR / "samples"
RESULTS_DIR = EVALS_DIR / "results"


def sample_id(sample: dict) -> str:
    return sample.get("file") or sample["text"][:40]


async def run_sample(graph, sample: dict) -> tuple[SampleResult, dict]:
    sid = sample_id(sample)
    try:
        images, caption = [], sample.get("caption")
        if "file" in sample:
            path = SAMPLES_DIR / sample["file"]
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            images.append(validate_image(mime, path.read_bytes()))
        else:
            caption = sample["text"]
        final = await run_analysis(
            graph,
            {"analysis_id": f"eval-{sid}", "caption": caption, "images": images},
            config={"run_name": "phishing-eval", "tags": ["eval"], "metadata": {"sample": sid}},
        )
        verdict = final.get("verdict")
        predicted = verdict.label if verdict else "help"
        detail = {"verdict": verdict.model_dump() if verdict else None}
        return SampleResult(sid, sample["expected"], predicted), detail
    except Exception as e:  # noqa: BLE001 - one broken sample must not stop the run
        return SampleResult(sid, sample["expected"], None, f"{type(e).__name__}: {e}"), {}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=EVALS_DIR / "labels.json")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args()

    load_dotenv()
    settings = Settings()
    configure_logging("WARNING", "console", None)

    samples = json.loads(args.labels.read_text(encoding="utf-8"))["samples"]
    print(f"{len(samples)} campioni, {2 * len(samples)} chiamate a {settings.anthropic_model}.")
    if not args.yes and input("Procedo? [s/N] ").strip().lower() not in {"s", "si", "sì", "y"}:
        return 1

    graph = build_graph(create_llm(settings))
    results, details = [], []
    for sample in samples:
        result, detail = await run_sample(graph, sample)
        mark = "✅" if is_correct(result.expected, result.predicted) else "❌"
        print(f"{mark} {result.sample_id:<42} atteso={result.expected:<24} "
              f"ottenuto={result.predicted or result.error}")
        results.append(result)
        details.append({**asdict(result), **detail})

    report = score(results)
    print(f"\nAccuratezza: {report.correct}/{report.total} ({report.accuracy:.0%})")
    print(f"Dangerous misses (truffa giudicata legittima): {report.dangerous_misses or 'nessuno'}")
    print(f"Errori: {report.errors or 'nessuno'}")
    print("Matrice di confusione (atteso → ottenuto):")
    for expected, row in report.confusion.items():
        print(f"  {expected}: {row}")

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(
        json.dumps({"report": asdict(report), "samples": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nRisultati salvati in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
