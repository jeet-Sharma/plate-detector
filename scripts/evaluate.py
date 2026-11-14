"""Compare a result JSON with a ground-truth JSON.

    python scripts/evaluate.py output/<clip>.plates.json eval/ground_truth/<clip>.json

A ground-truth plate counts as found when a result has exactly its text (or one of its
listed alternatives). Results that match nothing are split into near misses (within two
edits of a ground-truth plate: misreads and duplicate variants), hits on plates listed as
unreadable (not penalised), and false positives.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anpr.plate_format import edit_distance


def evaluate(result: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    found_plates = [p["plate"] for p in result["plates"]]
    gt = truth["plates"]

    def accepted(entry: dict[str, Any]) -> set[str]:
        return {entry["plate"], *entry.get("alternatives", [])}

    matched_results: set[str] = set()
    found, missed = [], []
    for entry in gt:
        hit = next((p for p in found_plates if p in accepted(entry)), None)
        if hit is None:
            missed.append(entry["plate"])
        else:
            found.append(entry["plate"])
            matched_results.add(hit)

    unreadable = [u["approx_text"] for u in truth.get("unreadable", [])]
    near_misses, ignored, false_positives = [], [], []
    for plate in found_plates:
        if plate in matched_results:
            continue
        best = min(
            (edit_distance(plate, a) for e in gt for a in accepted(e)), default=99
        )
        if best <= 2:
            near_misses.append(plate)
        elif any(edit_distance(plate, u) <= 2 for u in unreadable):
            ignored.append(plate)
        else:
            false_positives.append(plate)

    # Duplicates: more than one result within one edit of the same ground-truth plate.
    duplicates = 0
    for entry in gt:
        close = [p for p in found_plates if min(edit_distance(p, a) for a in accepted(entry)) <= 1]
        duplicates += max(0, len(close) - 1)

    # Character errors: 0 for a found plate; the edit distance for a near miss (within two
    # edits); every character for a plate that was not found at all.
    total_chars = sum(len(e["plate"]) for e in gt) or 1
    char_errors = 0
    for e in gt:
        best = min(
            (min(edit_distance(p, a) for a in accepted(e)) for p in found_plates),
            default=len(e["plate"]),
        )
        char_errors += best if best <= 2 else len(e["plate"])
    n_results = len(found_plates)
    return {
        "video": truth.get("video"),
        "ground_truth_plates": len(gt),
        "reported_plates": n_results,
        "found": found,
        "missed": missed,
        "recall": round(len(found) / len(gt), 3) if gt else 1.0,
        "precision": round(len(found) / n_results, 3) if n_results else 1.0,
        "near_misses": near_misses,
        "false_positives": false_positives,
        "unreadable_hits_ignored": ignored,
        "duplicate_variants": duplicates,
        "character_error_rate": round(char_errors / total_chars, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("result_json")
    parser.add_argument("ground_truth_json")
    args = parser.parse_args(argv)
    report = evaluate(
        json.loads(Path(args.result_json).read_text(encoding="utf-8")),
        json.loads(Path(args.ground_truth_json).read_text(encoding="utf-8")),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
