import numpy as np
import pytest

from anpr.plate_format import (
    FALLBACK_WEIGHT,
    edit_distance,
    match_format,
    normalize,
    resolve,
)
from anpr.types import PlateAlphabet

pytestmark = pytest.mark.unit

ALPHABET = PlateAlphabet("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_")
SLOTS = 10


def probs_for(text: str, confidence: float = 0.9) -> np.ndarray:
    """Slot probabilities that put `confidence` on the given text, rest spread evenly."""
    p = np.full((SLOTS, len(ALPHABET.chars)), (1 - confidence) / (len(ALPHABET.chars) - 1))
    for slot in range(SLOTS):
        ch = text[slot] if slot < len(text) else "_"
        p[slot, ALPHABET.chars.index(ch)] = confidence
    return p


def test_normalize_strips_and_uppercases():
    assert normalize(" 9kta-262 ") == "9KTA262"
    assert normalize("7ZZV 186.") == "7ZZV186"
    assert normalize("") == ""


def test_match_format_california_standard():
    fmt = match_format("9KTA262")
    assert fmt is not None
    assert fmt.weight == 1.0


def test_match_format_rejects_wrong_shape():
    assert match_format("9KTA26") is None
    assert match_format("KTA9262") is None
    assert match_format("") is None


def test_edit_distance():
    assert edit_distance("9KTA262", "9KTA262") == 0
    assert edit_distance("9KTA262", "9KTA282") == 1
    assert edit_distance("9KTA262", "9KTA26") == 1
    assert edit_distance("", "ABC") == 3


def test_resolve_valid_text_unchanged():
    text, weight = resolve(probs_for("9KTA262"), ALPHABET, 7)
    assert text == "9KTA262"
    assert weight == 1.0


def test_resolve_fixes_letter_read_as_digit_by_position():
    # Slot 1 must be a letter. The model read '0' there, but O is the next best.
    p = probs_for("90TA262", 0.8)
    p[1, ALPHABET.chars.index("O")] = 0.15
    text, weight = resolve(p, ALPHABET, 7)
    assert text == "9OTA262"
    assert weight == 1.0


def test_resolve_fixes_digit_read_as_letter_by_position():
    p = probs_for("9KTAZ62", 0.8)  # last-three slots must be digits; slot 4 read as Z
    p[4, ALPHABET.chars.index("2")] = 0.15
    text, _ = resolve(p, ALPHABET, 7)
    assert text == "9KTA262"


def test_resolve_keeps_unusual_plate_with_fallback_weight():
    text, weight = resolve(probs_for("LOVE"), ALPHABET, 4)
    assert text == "LOVE"
    assert weight == FALLBACK_WEIGHT


def test_resolve_does_not_force_far_off_reading_into_template():
    # Needs more than max_changes slot changes to become DLLLDDD, so it is kept as read.
    text, weight = resolve(probs_for("AB1CD2E"), ALPHABET, 7)
    assert text == "AB1CD2E"
    assert weight == FALLBACK_WEIGHT


def test_resolve_never_returns_pad_character():
    text, _ = resolve(probs_for("AB"), ALPHABET, 2)
    assert "_" not in text


def test_motorcycle_plate_matches_its_own_template():
    fmt = match_format("8D94661")
    assert fmt is not None
    assert fmt.name == "california-motorcycle"
    text, weight = resolve(probs_for("8D94661"), ALPHABET, 7)
    assert text == "8D94661"
    assert weight == fmt.weight


def test_confident_reading_is_not_forced_into_a_template():
    # D L D L D D D fits no template. Fixing it to DLLLDDD would overrule a confident model.
    text, weight = resolve(probs_for("8D9X661", 0.95), ALPHABET, 7)
    assert text == "8D9X661"
    assert weight == FALLBACK_WEIGHT


def test_uncertain_slot_is_fixed_to_template_class():
    # Slot 2 should be a letter. The model is unsure: '9' at 0.4, 'K' at 0.35.
    p = probs_for("8D9X661", 0.9)
    p[2] = 0.001
    p[2, ALPHABET.chars.index("9")] = 0.4
    p[2, ALPHABET.chars.index("K")] = 0.35
    p[3] = 0.001
    p[3, ALPHABET.chars.index("X")] = 0.9
    text, weight = resolve(p, ALPHABET, 7)
    assert text == "8DKX661"
    assert weight == 1.0
