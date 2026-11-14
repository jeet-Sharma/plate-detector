import itertools
import time

import pytest

from anpr.detector import suppress_duplicates, tile_positions
from anpr.types import Box, Detection
from anpr.video import prefetch

pytestmark = pytest.mark.unit


def test_tile_positions_cover_the_length_with_overlap():
    positions = tile_positions(1280, 640, 96)
    assert positions[0] == 0
    assert positions[-1] + 640 == 1280
    for a, b in itertools.pairwise(positions):
        assert a + 640 - b >= 96  # overlap is at least the requested amount


def test_tile_positions_short_length_is_one_tile():
    assert tile_positions(500, 640, 96) == [0]
    assert tile_positions(640, 640, 96) == [0]


def test_suppress_duplicates_keeps_higher_score_of_overlapping_pair():
    a = Detection(Box(100, 100, 200, 150), 0.9)
    b = Detection(Box(102, 101, 202, 151), 0.7)
    kept = suppress_duplicates([b, a])
    assert kept == [a]


def test_suppress_duplicates_removes_cut_off_copy_inside_a_better_box():
    full = Detection(Box(100, 100, 220, 160), 0.9)
    cut = Detection(Box(100, 100, 150, 160), 0.8)  # same plate, clipped by a tile edge
    assert suppress_duplicates([cut, full]) == [full]


def test_suppress_duplicates_keeps_separate_plates():
    a = Detection(Box(0, 0, 100, 50), 0.9)
    b = Detection(Box(500, 500, 600, 550), 0.8)
    assert len(suppress_duplicates([a, b])) == 2


def test_prefetch_yields_everything_in_order():
    assert list(prefetch(iter(range(20)), depth=2)) == list(range(20))


def test_prefetch_forwards_producer_errors():
    def broken():
        yield 1
        raise RuntimeError("decode failed")

    it = prefetch(broken())
    assert next(it) == 1
    with pytest.raises(RuntimeError, match="decode failed"):
        next(it)


def test_prefetch_stops_producer_when_consumer_quits():
    produced = []

    def source():
        for i in range(1000):
            produced.append(i)
            yield i

    gen = prefetch(source(), depth=2)
    assert next(gen) == 0
    gen.close()
    time.sleep(0.3)
    assert len(produced) < 20
