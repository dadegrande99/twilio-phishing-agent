import pytest

from evals.scoring import SampleResult, score


def r(sample_id, expected, predicted, error=None):
    return SampleResult(sample_id, expected, predicted, error)


def test_accuracy_and_sospetto_counts_for_phishing():
    report = score(
        [
            r("a", "phishing", "phishing"),
            r("b", "phishing", "sospetto"),
            r("c", "probabilmente_legittimo", "sospetto"),
        ]
    )
    assert report.total == 3 and report.correct == 2
    assert report.accuracy == pytest.approx(2 / 3)


def test_dangerous_miss():
    assert score([r("a", "phishing", "probabilmente_legittimo")]).dangerous_misses == ["a"]
    assert score([r("b", "sospetto", "probabilmente_legittimo")]).dangerous_misses == ["b"]


def test_errors_counted_as_wrong():
    report = score([r("a", "phishing", None, "timeout")])
    assert report.correct == 0 and report.errors == ["a"]


def test_confusion():
    assert score([r("a", "phishing", "sospetto")]).confusion == {"phishing": {"sospetto": 1}}


def test_empty_results():
    report = score([])
    assert report.total == 0 and report.accuracy == 0.0
