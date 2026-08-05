#!/usr/bin/env python3
"""Update routine data-check metadata on the latest main branch.

This script intentionally has no option for ``matrixUpdatedAt``.
That field means "matrix system update" and is reserved for substantial
structural or functional changes defined in SYSTEM_UPDATE_POLICY.md.
Routine reservation, charger, TMAP, subsidy, and competitor-price updates must never
change it.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


VERSION_PATH = Path("version.json")
SEOUL = ZoneInfo("Asia/Seoul")


def iso_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"Timezone is required: {value}")
    return parsed.astimezone(SEOUL).isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reservation-updated-at", type=iso_timestamp)
    parser.add_argument("--charger-checked-at", type=iso_timestamp)
    parser.add_argument("--tmap-checked-at", type=iso_timestamp)
    parser.add_argument("--charger-updated-at", type=iso_timestamp)
    parser.add_argument("--competitor-price-checked-at", type=iso_timestamp)
    parser.add_argument("--subsidy-checked-at", type=iso_timestamp)
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.error("At least one metadata timestamp is required")
    return args


def main() -> None:
    args = parse_args()
    data = json.loads(VERSION_PATH.read_text(encoding="utf-8"))
    parts = data["version"].split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    data["version"] = ".".join(parts)
    data["updatedAt"] = datetime.now(SEOUL).isoformat(timespec="seconds")

    field_values = {
        "reservationUpdatedAt": args.reservation_updated_at,
        "chargerCheckedAt": args.charger_checked_at,
        "tmapCheckedAt": args.tmap_checked_at,
        "chargerUpdatedAt": args.charger_updated_at,
        "competitorPriceCheckedAt": args.competitor_price_checked_at,
        "subsidyCheckedAt": args.subsidy_checked_at,
    }
    for field, value in field_values.items():
        if value is not None:
            data[field] = value

    VERSION_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
