"""Tracker features the live UI relies on: stable ids, snapshots, per-character confidence."""

import pytest

from anpr.plate_format import edit_distance, within_one_edit
from anpr.tracker import Tracker, TrackerConfig
from tests.test_tracker import ALPHABET, box_at, make_reading

pytestmark = pytest.mark.unit


def new_tracker(**kw) -> Tracker:
    return Tracker(TrackerConfig(alphabet=ALPHABET, **kw))


def test_update_returns_a_stable_track_id_per_reading():
    t = new_tracker()
    first = t.update(
        0.0,
        [make_reading(0.0, "9KTA262", box_at(100)), make_reading(0.0, "7ZZV186", box_at(1500))],
    )
    second = t.update(
        0.2,
        [make_reading(0.2, "7ZZV186", box_at(1510)), make_reading(0.2, "9KTA262", box_at(105))],
    )
    assert len(first) == 2 and first[0] != first[1]
    assert second == [first[1], first[0]]  # same plates keep their ids, whatever the order
    assert t.update(0.4, []) == []


def test_snapshot_matches_finalize_and_does_not_end_tracks():
    t = new_tracker()
    for i in range(3):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    assert [p.plate for p in t.snapshot()] == ["9KTA262"]
    t.update(0.6, [make_reading(0.6, "9KTA262", box_at(105))])
    assert t.snapshot()[0].readings == 4  # the track was still open
    assert t.finalize()[0].readings == 4


def test_results_carry_track_ids_and_per_character_confidence():
    t = new_tracker()
    for i in range(3):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    r = t.finalize()[0]
    assert len(r.track_ids) == 1
    assert len(r.char_confidence) == 7
    assert all(0.0 <= c <= 1.0 for c in r.char_confidence)


def test_merged_results_list_every_merged_track():
    t = new_tracker(max_age=1)
    for i in range(10):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA262", box_at(100))])
    for i in range(10, 14):
        t.update(i * 0.2, [])
    for i in range(14, 17):
        t.update(i * 0.2, [make_reading(i * 0.2, "9KTA282", box_at(2500))])
    assert len(t.finalize()[0].track_ids) == 2


def test_cached_votes_follow_the_time_span_when_a_frame_is_skipped():
    t = new_tracker()
    t.update(0.0, [make_reading(0.0, "9KTA262", box_at(100))])
    assert t.snapshot()[0].last_seen == 0.0
    t.touch(0.4)
    assert t.snapshot()[0].last_seen == pytest.approx(0.4)


def test_within_one_edit_is_consistent_with_edit_distance():
    cases = ["9KTA262", "9KTA282", "9KTA26", "9KTA2622", "7ZZV186", "", "A", "9KT262"]
    for a in cases:
        for b in cases:
            assert within_one_edit(a, b) == min(edit_distance(a, b), 2), (a, b)
