"""Track plates across sampled frames and vote per character into one result each.

No OpenCV or ONNX Runtime here, so the logic stays fast to test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

import numpy as np

from anpr.plate_format import match_format, resolve, within_one_edit
from anpr.types import Box, PlateAlphabet, PlateResult, Reading

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackerConfig:
    alphabet: PlateAlphabet
    iou_threshold: float = 0.2
    max_age: int = 3  # processed frames a track may go unmatched before it ends
    keep_best: int = 15  # readings kept per track for voting
    merge_ratio: float = 0.5  # absorb a near-duplicate with fewer than this share of readings
    merge_gap_s: float = 2.0  # near-duplicates this close in time are one plate
    near_distance_factor: float = 1.5  # centre distance limit, in box widths, for text matching


@dataclass
class _Track:
    first_seen: float
    last_seen: float
    last_box: Box
    id: int = 0
    readings: list[Reading] = field(default_factory=list)
    count: int = 0
    missed: int = 0
    cached: _Candidate | None = None  # vote result for `cached_count` readings
    cached_count: int = -1

    def add(self, reading: Reading, keep_best: int) -> None:
        self.readings.append(reading)
        self.readings.sort(key=lambda r: r.weight, reverse=True)
        del self.readings[keep_best:]
        self.count += 1
        self.last_seen = reading.timestamp
        self.last_box = reading.box
        self.missed = 0


@dataclass
class _Candidate:
    """A finished track reduced to text, vote statistics and time span."""

    agg: np.ndarray  # (slots, alphabet), mean probability over the kept readings
    length: int
    mean_confidence: float
    count: int
    first_seen: float
    last_seen: float
    track_ids: tuple[int, ...] = ()
    text: str = ""
    weight: float = 1.0


def _gap(a: _Candidate, b: _Candidate) -> float:
    """Seconds between two time spans (0 when they overlap)."""
    return max(0.0, max(a.first_seen, b.first_seen) - min(a.last_seen, b.last_seen))


class Tracker:
    def __init__(self, config: TrackerConfig) -> None:
        self.cfg = config
        self._active: list[_Track] = []
        self._done: list[_Track] = []
        self._next_id = 1

    # ---- per-frame updates -------------------------------------------------------

    def update(self, timestamp: float, readings: list[Reading]) -> list[int]:
        """Match this frame's readings to active tracks, start new ones, age the rest.

        Returns the track id of each reading, in the same order as `readings`.
        """
        matched: set[int] = set()
        used: set[int] = set()
        assigned: dict[int, int] = {}
        pairs = sorted(
            (
                (r.box.iou(t.last_box), ri, ti)
                for ri, r in enumerate(readings)
                for ti, t in enumerate(self._active)
            ),
            reverse=True,
        )
        for iou, ri, ti in pairs:
            if iou < self.cfg.iou_threshold:
                break
            if ri in used or ti in matched:
                continue
            self._active[ti].add(readings[ri], self.cfg.keep_best)
            assigned[ri] = self._active[ti].id
            used.add(ri)
            matched.add(ti)

        # Second chance: same text (edit distance <= 1) and close together.
        for ri, r in enumerate(readings):
            if ri in used:
                continue
            ti = self._match_by_text(r, matched)
            if ti is not None:
                self._active[ti].add(r, self.cfg.keep_best)
                assigned[ri] = self._active[ti].id
                used.add(ri)
                matched.add(ti)

        for ri, r in enumerate(readings):
            if ri not in used:
                track = _Track(timestamp, timestamp, r.box, id=self._next_id)
                self._next_id += 1
                track.add(r, self.cfg.keep_best)
                self._active.append(track)
                assigned[ri] = track.id

        still_active: list[_Track] = []
        for ti, track in enumerate(self._active):
            if ti not in matched and track.last_seen != timestamp:
                track.missed += 1
            if track.missed > self.cfg.max_age:
                self._done.append(track)
            else:
                still_active.append(track)
        self._active = still_active
        return [assigned[ri] for ri in range(len(readings))]

    def touch(self, timestamp: float) -> None:
        """A frame was skipped because the scene did not change: plates seen in the last
        processed frame are still visible, so extend their end time without aging them."""
        for track in self._active:
            if track.missed == 0:
                track.last_seen = timestamp

    def _text_of(self, track: _Track) -> str:
        cand = self._candidate(track)
        return cand.text if cand else ""

    def _match_by_text(self, reading: Reading, taken: set[int]) -> int | None:
        text = self.cfg.alphabet.decode(reading.probs)
        if not text:
            return None
        cx, cy = reading.box.center
        best: tuple[float, int] | None = None
        for ti, track in enumerate(self._active):
            if ti in taken:
                continue
            tx, ty = track.last_box.center
            dist = float(np.hypot(cx - tx, cy - ty))
            if dist > self.cfg.near_distance_factor * max(reading.box.width, track.last_box.width):
                continue
            if within_one_edit(text, self._text_of(track)) <= 1 and (
                best is None or dist < best[0]
            ):
                best = (dist, ti)
        return best[1] if best else None

    # ---- end of video ------------------------------------------------------------

    def snapshot(self) -> list[PlateResult]:
        """Current results without ending anything: the same vote and merge as `finalize`,
        safe to call while the video is still being processed."""
        return self.finalize()

    def finalize(self) -> list[PlateResult]:
        """Vote inside each track, merge duplicates across tracks, return results."""
        candidates = [
            c for t in self._done + self._active if (c := self._candidate(t)) is not None
        ]
        merged = self._merge(candidates)
        merged.sort(key=lambda c: c.first_seen)
        return [self._to_result(c) for c in merged]

    def _candidate(self, track: _Track) -> _Candidate | None:
        if not track.readings:
            return None
        if track.cached_count == track.count:  # votes unchanged; only the time span may move
            if track.cached is None:
                return None
            return replace(track.cached, first_seen=track.first_seen, last_seen=track.last_seen)
        track.cached = self._vote(track)
        track.cached_count = track.count
        return track.cached

    def _vote(self, track: _Track) -> _Candidate | None:
        alphabet = self.cfg.alphabet
        weight_by_length: dict[int, float] = {}
        for r in track.readings:
            length = len(alphabet.decode(r.probs))
            weight_by_length[length] = weight_by_length.get(length, 0.0) + r.weight
        length = max(weight_by_length, key=lambda k: weight_by_length[k])
        if length == 0:
            return None
        same = [r for r in track.readings if len(alphabet.decode(r.probs)) == length]
        total = sum(r.weight for r in same) or 1.0
        agg = sum(r.probs * (r.weight / total) for r in same)
        cand = _Candidate(
            agg=agg,
            length=length,
            mean_confidence=float(np.mean([r.confidence for r in same])),
            count=track.count,
            first_seen=track.first_seen,
            last_seen=track.last_seen,
            track_ids=(track.id,),
        )
        cand.text, cand.weight = resolve(agg, alphabet, length)
        return cand

    def _merge(self, candidates: list[_Candidate]) -> list[_Candidate]:
        pool = list(candidates)
        changed = True
        while changed:
            changed = False
            pool.sort(key=lambda c: c.count, reverse=True)
            for i, big in enumerate(pool):
                for j in range(len(pool) - 1, i, -1):
                    small = pool[j]
                    dist = within_one_edit(big.text, small.text)
                    if dist == 0 or (
                        dist == 1
                        and big.length == small.length
                        and (
                            small.count < self.cfg.merge_ratio * big.count
                            or _gap(big, small) <= self.cfg.merge_gap_s
                        )
                    ):
                        pool[i] = self._combine(big, small)
                        del pool[j]
                        changed = True
                        break
                if changed:
                    break
        return pool

    def _combine(self, big: _Candidate, small: _Candidate) -> _Candidate:
        total = big.count + small.count
        agg = (big.agg * big.count + small.agg * small.count) / total
        merged = _Candidate(
            agg=agg,
            length=big.length,
            mean_confidence=(big.mean_confidence * big.count + small.mean_confidence * small.count)
            / total,
            count=total,
            first_seen=min(big.first_seen, small.first_seen),
            last_seen=max(big.last_seen, small.last_seen),
            track_ids=big.track_ids + small.track_ids,
        )
        merged.text, merged.weight = resolve(agg, self.cfg.alphabet, big.length)
        log.debug("merged %s into %s", small.text, big.text)
        return merged

    def _to_result(self, cand: _Candidate) -> PlateResult:
        alphabet = self.cfg.alphabet
        slots = range(cand.length)
        shares = [float(cand.agg[s, alphabet.chars.index(cand.text[s])]) for s in slots]
        share = float(np.mean(shares))
        confidence = min(1.0, max(0.0, share * cand.mean_confidence * cand.weight))
        return PlateResult(
            plate=cand.text,
            first_seen=cand.first_seen,
            last_seen=cand.last_seen,
            confidence=round(confidence, 4),
            readings=cand.count,
            matches_format=match_format(cand.text) is not None,
            track_ids=cand.track_ids,
            char_confidence=tuple(round(x, 3) for x in shares),
        )
