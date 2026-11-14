"""Console, JSON and CSV writers."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from anpr.types import PlateResult


def format_timestamp(seconds: float) -> str:
    """mm:ss.sss (minutes keep growing past 59 for long videos)."""
    millis = round(max(0.0, seconds) * 1000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def plate_row(result: PlateResult) -> dict[str, Any]:
    return {
        "plate": result.plate,
        "first_seen": format_timestamp(result.first_seen),
        "last_seen": format_timestamp(result.last_seen),
        "confidence": round(result.confidence, 3),
        "readings": result.readings,
    }


def write_json(path: Path, results: list[PlateResult], run: dict[str, Any]) -> None:
    payload = {"plates": [plate_row(r) for r in results], "run": run}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, results: list[PlateResult]) -> None:
    fields = ["plate", "first_seen", "last_seen", "confidence", "readings"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plate_row(r) for r in results)


def console_table(results: list[PlateResult]) -> str:
    if not results:
        return "No plates found."
    rows = [plate_row(r) for r in results]
    header = f"{'PLATE':<12}{'FIRST SEEN':<12}{'LAST SEEN':<12}{'CONF':<7}READINGS"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['plate']:<12}{r['first_seen']:<12}{r['last_seen']:<12}"
            f"{r['confidence']:<7.2f}{r['readings']}"
        )
    return "\n".join(lines)


def run_to_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
