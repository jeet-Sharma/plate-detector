"""Plate text rules: normalisation, format templates and position-aware fixes.

Pure functions. No OpenCV, no ONNX Runtime. Formats are data: add a country by adding
templates to ``TEMPLATES``. A template is a string of ``D`` (digit) and ``L`` (letter).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from anpr.types import PlateAlphabet

DIGITS = "0123456789"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Weight applied to confidence for text that matches no template. Vanity and out-of-state
# plates are legitimate, so this stays high enough that they survive the confidence filter.
FALLBACK_WEIGHT = 0.75

# A template-conforming rewrite replaces what the model read only when it costs at most
# log(PRIOR_BOOST) nats of model probability: the template is PRIOR_BOOST times more likely
# a priori than an unusual plate. A confident reading is therefore never overruled.
PRIOR_BOOST = 3.0
# Visually similar pairs: the model's probabilities are unreliable here, so a fix between
# them costs at most this many nats per slot.
CONFUSABLE_COST = 0.3
CONFUSABLE_PAIRS = frozenset(
    frozenset(pair) for pair in ("0O", "0D", "1I", "8B", "5S", "2Z", "6G")
)
_MIN_PROB = 1e-6


@dataclass(frozen=True)
class PlateFormat:
    name: str
    template: str  # 'D' = digit slot, 'L' = letter slot
    weight: float

    def matches(self, text: str) -> bool:
        if len(text) != len(self.template):
            return False
        return all(
            (ch.isdigit() if kind == "D" else ch.isalpha() and ch.isascii())
            for ch, kind in zip(text, self.template, strict=True)
        )


TEMPLATES: tuple[PlateFormat, ...] = (
    PlateFormat("california-standard", "DLLLDDD", 1.0),
    PlateFormat("california-motorcycle", "DLDDDDD", 0.95),
    PlateFormat("california-older-3L3D", "LLLDDD", 0.9),
    PlateFormat("california-older-3D3L", "DDDLLL", 0.9),
)

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def normalize(text: str) -> str:
    """Uppercase and drop everything except A-Z and 0-9."""
    return _NON_ALNUM.sub("", text.upper())


def match_format(text: str) -> PlateFormat | None:
    """Highest-weight template whose letter/digit pattern fits `text`, or None."""
    fits = [fmt for fmt in TEMPLATES if fmt.matches(text)]
    return max(fits, key=lambda f: f.weight) if fits else None


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def within_one_edit(a: str, b: str) -> int:
    """0 if equal, 1 if exactly one edit apart, 2 if further. Linear time, for hot loops."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return 2
    if la == lb:
        return 1 if sum(x != y for x, y in zip(a, b, strict=True)) == 1 else 2
    short, long_ = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return 1 if short[i:] == long_[i + 1 :] else 2


def _best_in_class(row: np.ndarray, alphabet: PlateAlphabet, allowed: str) -> tuple[str, float]:
    idx = [alphabet.chars.index(c) for c in allowed if c in alphabet.chars]
    best = idx[int(np.argmax(row[idx]))]
    return alphabet.chars[best], float(row[best])


def _slot_cost(row: np.ndarray, alphabet: PlateAlphabet, raw: str, cand: str) -> float:
    """Model probability lost (in nats) by writing `cand` instead of `raw` in one slot."""
    if raw == cand:
        return 0.0
    p_raw = max(float(row[alphabet.chars.index(raw)]), _MIN_PROB)
    p_cand = max(float(row[alphabet.chars.index(cand)]), _MIN_PROB)
    cost = max(0.0, math.log(p_raw) - math.log(p_cand))
    if frozenset((raw, cand)) in CONFUSABLE_PAIRS:
        return min(cost, CONFUSABLE_COST)
    return cost


def resolve(
    probs: np.ndarray,
    alphabet: PlateAlphabet,
    length: int,
    max_changes: int = 2,
) -> tuple[str, float]:
    """Choose final text for the first `length` slots and its format weight.

    1. Take the best non-padding character per slot.
    2. If that text fits a template, keep it.
    3. Otherwise try each template of the same length: in every slot take the most
       probable character of the required class (letter or digit). Accept the cheapest
       candidate that changes at most `max_changes` slots and costs at most
       ``log(PRIOR_BOOST)`` nats (see the constants above).
    4. Otherwise keep the text as read with ``FALLBACK_WEIGHT``.
    """
    allowed = "".join(c for c in alphabet.chars if c != alphabet.pad)
    raw = "".join(_best_in_class(probs[i], alphabet, allowed)[0] for i in range(length))

    fmt = match_format(raw)
    if fmt is not None:
        return raw, fmt.weight

    budget = math.log(PRIOR_BOOST)
    best: tuple[float, float, str, float] | None = None  # (cost, -weight, text, weight)
    for fmt in TEMPLATES:
        if len(fmt.template) != length:
            continue
        chars = [
            _best_in_class(probs[slot], alphabet, DIGITS if kind == "D" else LETTERS)[0]
            for slot, kind in enumerate(fmt.template)
        ]
        candidate = "".join(chars)
        changes = sum(a != b for a, b in zip(candidate, raw, strict=True))
        if changes > max_changes:
            continue
        cost = sum(_slot_cost(probs[s], alphabet, raw[s], candidate[s]) for s in range(length))
        if cost > budget:
            continue
        key = (cost, -fmt.weight, candidate, fmt.weight)
        if best is None or key[:2] < best[:2]:
            best = key
    if best is not None:
        return best[2], best[3]
    return raw, FALLBACK_WEIGHT
