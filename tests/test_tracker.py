import numpy as np
import pytest

from anpr.tracker import Tracker, TrackerConfig
from anpr.types import Box, PlateAlphabet, Reading

pytestmark = pytest.mark.unit

ALPHABET = PlateAlphabet("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_")
SLOTS = 10


def make_reading(
    ts: float, text: str, box: Box, confidence: float = 0.9, weight: float = 0.8
) -> Reading:
    p = np.full((SLOTS, len(ALPHABET.chars)), 0.1 / (len(ALPHABET.chars) - 1))
    for slot in range(SLOTS):
        ch = text[slot] if slot < len(text) else "_"
        p[slot, ALPHABET.chars.index(ch)] = 0.9
    return Reading(ts, box, p, confidence, weight)


def box_at(x: float, y: float = 500.0) -> Box:
    return Box(x, y, x + 100, y + 50)


def new_tracker(**kw) -> Tracker:
    return Tracker(TrackerConfig(alphabet=ALPHABET, **kw))


def test_empty_tracker_returns_nothing():
    assert new_tracker().finalize() == []


def test_same_plate_over_frames_is_one_result():
    t = new_tracker()
    for i in range(5):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100 + i * 10))])
    results = t.finalize()
    assert len(results) == 1
    r = results[0]
    assert r.plate == "9KTA262"
    assert r.readings == 5
    assert r.first_seen == 0.0
    assert r.last_seen == pytest.approx(0.8)
    assert 0.0 < r.confidence <= 1.0


def test_voting_corrects_one_noisy_reading():
    t = new_tracker()
    texts = ["9KTA262", "9KTA262", "9KTA282", "9KTA262", "9KTA262"]
    for i, text in enumerate(texts):
        t.update(i * 0.2, [make_reading(i * 0.2, text, box_at(100 + i * 5))])
    assert t.finalize()[0].plate == "9KTA262"


def test_two_plates_in_same_frame_stay_separate():
    t = new_tracker()
    for i in range(3):
        ts = i * 0.2
        t.update(
            ts,
            [
                make_reading(ts, "9KTA262", box_at(100)),
                make_reading(ts, "7ZZV186", box_at(1500)),
            ],
        )
    plates = sorted(r.plate for r in t.finalize())
    assert plates == ["7ZZV186", "9KTA262"]


def test_track_that_ended_and_returns_is_merged():
    t = new_tracker(max_age=2)
    for i in range(3):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    for i in range(3, 8):  # plate out of view long enough to end the track
        t.update(i * 0.2, [])
    for i in range(8, 11):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(2000))])
    results = t.finalize()
    assert len(results) == 1
    assert results[0].readings == 6
    assert results[0].first_seen == 0.0
    assert results[0].last_seen == pytest.approx(2.0)


def test_small_near_duplicate_is_absorbed():
    t = new_tracker(max_age=1)
    for i in range(10):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    for i in range(10, 14):
        t.update(i * 0.2, [])
    for i in range(14, 17):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA282", box_at(2500))])
    results = t.finalize()
    assert [r.plate for r in results] == ["9KTA262"]
    assert results[0].readings == 13


def test_comparable_near_duplicates_far_apart_in_time_stay_separate():
    t = new_tracker(max_age=1, merge_gap_s=2.0)
    for i in range(5):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    for i in range(5, 25):  # a long gap, then a plate one character away
        t.update(i * 0.2, [])
    for i in range(25, 29):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA282", box_at(2500))])
    assert len(t.finalize()) == 2


def test_comparable_near_duplicates_close_in_time_are_one_plate():
    t = new_tracker(max_age=1, merge_gap_s=2.0)
    for i in range(4):
        t.update(i * 0.2, [make_reading(i * 0.2, "8DSA661", box_at(100))])
    for i in range(4, 7):
        t.update(i * 0.2, [])
    for i in range(7, 10):
        t.update(i * 0.2, [make_reading(i * 0.2, "8DSL661", box_at(2500))])
    results = t.finalize()
    assert len(results) == 1
    assert results[0].readings == 7


def test_touch_extends_last_seen_without_adding_readings():
    t = new_tracker()
    t.update(0.0, [make_reading(0.0, "9KTA262", box_at(100))])
    t.touch(0.2)
    t.touch(0.4)
    r = t.finalize()[0]
    assert r.readings == 1
    assert r.last_seen == pytest.approx(0.4)


def test_unusual_length_plate_is_kept():
    t = new_tracker()
    for i in range(3):
        t.update(i * 0.2, [make_reading(i * 0.2, "LOVE", box_at(100))])
    r = t.finalize()[0]
    assert r.plate == "LOVE"
    assert not r.matches_format


def test_keeps_only_best_readings_but_counts_all():
    t = new_tracker(keep_best=3)
    for i in range(10):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    assert t.finalize()[0].readings == 10
