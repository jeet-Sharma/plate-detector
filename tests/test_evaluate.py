import pytest

from scripts.evaluate import evaluate

pytestmark = pytest.mark.unit

TRUTH = {
    "video": "x.mp4",
    "plates": [
        {"plate": "9KTA262"},
        {"plate": "9ECF543", "alternatives": ["9FCF543"]},
        {"plate": "7ZZV186"},
    ],
    "unreadable": [{"approx_text": "JYFLSS"}],
}


def result(*plates: str) -> dict:
    return {"plates": [{"plate": p} for p in plates]}


def test_perfect_result():
    r = evaluate(result("9KTA262", "9FCF543", "7ZZV186"), TRUTH)
    assert r["recall"] == 1.0
    assert r["precision"] == 1.0
    assert r["missed"] == []
    assert r["character_error_rate"] == 0.0


def test_missing_plate_lowers_recall():
    r = evaluate(result("9KTA262"), TRUTH)
    assert r["recall"] == pytest.approx(0.333, abs=0.001)
    assert sorted(r["missed"]) == ["7ZZV186", "9FCF543".replace("FCF", "ECF")]


def test_near_miss_and_duplicate_variants_are_reported():
    r = evaluate(result("9KTA262", "9KTA282", "9ECF543", "7ZZV186"), TRUTH)
    assert r["near_misses"] == ["9KTA282"]
    assert r["duplicate_variants"] == 1
    assert r["precision"] == 0.75


def test_unreadable_hit_is_ignored_and_other_extras_are_false_positives():
    r = evaluate(result("9KTA262", "9ECF543", "7ZZV186", "JYFLSS", "QQQ1234"), TRUTH)
    assert r["unreadable_hits_ignored"] == ["JYFLSS"]
    assert r["false_positives"] == ["QQQ1234"]


def test_empty_result_is_handled():
    r = evaluate(result(), TRUTH)
    assert r["recall"] == 0.0
    assert r["precision"] == 1.0
