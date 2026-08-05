#!/usr/bin/env python3
"""Build the nationwide EV subsidy snapshot from rendered EV portal HTML.

The EV portal protects its HTML with client-side PNP rendering, so the workflow
first renders the two official pages in headless Chrome and then passes the
resulting DOM files to this script.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lxml import html


SEOUL = timezone(timedelta(hours=9))
PAYMENT_URL = "https://ev.or.kr/nportal/buySupprt/initSubsidyPaymentCheckAction.do"
PRICE_URL = "https://ev.or.kr/nportal/buySupprt/initPsLocalCarPirceAction.do"
NATIONAL_MAX_MANWON = 648

SIDO_NAMES = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
    "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
    "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도",
    "경남": "경상남도", "제주": "제주특별자치도",
}


def clean_text(node) -> str:
    return re.sub(r"\s+", " ", " ".join(node.itertext())).strip()


def first_int(text: str) -> int:
    match = re.search(r"-?[\d,]+", text or "")
    return int(match.group(0).replace(",", "")) if match else 0


def table_rows(path: Path, caption_text: str) -> list[list[str]]:
    root = html.parse(str(path))
    tables = root.xpath(
        "//table[caption[contains(normalize-space(.), $caption)]]",
        caption=caption_text,
    )
    if not tables:
        raise RuntimeError(f"공식 표를 찾지 못했습니다: {caption_text}")
    rows: list[list[str]] = []
    for row in tables[0].xpath("(.//tr)[position() > 1]"):
        cells = [clean_text(cell) for cell in row.xpath("./th|./td")]
        if cells:
            rows.append(cells)
    return rows


def build_snapshot(payment_html: Path, price_html: Path, model_html: Path | None = None) -> dict:
    payments = table_rows(payment_html, "지자체별 무공해차 구매보조금 지급현황")
    prices = table_rows(price_html, "전기자동차 지자체 차종별 보조금 목록")
    price_by_region = {
        row[1]: first_int(row[3])
        for row in prices
        if len(row) >= 4 and row[0] != "공단"
    }

    regions = []
    for row in payments:
        if len(row) < 9 or row[0] == "공단":
            continue
        combined = price_by_region.get(row[1], 0)
        regions.append({
            "sido": SIDO_NAMES.get(row[0], row[0]),
            "sigungu": row[1],
            "announced": first_int(row[5]),
            "received": first_int(row[6]),
            "delivered": first_int(row[7]),
            "remaining": first_int(row[8]),
            "nationalMaxManwon": NATIONAL_MAX_MANWON,
            "localMaxManwon": max(0, combined - NATIONAL_MAX_MANWON),
            "combinedMaxManwon": combined,
            "applicationMethod": row[4].lstrip("*"),
            "notice": row[3],
        })

    if len(regions) < 150:
        raise RuntimeError(f"전국 데이터가 불완전합니다: {len(regions)}개 지역")

    official_models = []
    if model_html and model_html.exists():
        model_rows = table_rows(model_html, "전기자동차 모델별 보조금 목록")
        for row in model_rows:
            if len(row) < 6:
                continue
            if "볼보" in row[1] and "ES90" in row[2].upper():
                official_models.append({
                    "modelName": row[2],
                    "nationalManwon": first_int(row[3]),
                    "seoulLocalManwon": first_int(row[4]),
                    "seoulCombinedManwon": first_int(row[5]),
                })

    officially_listed = bool(official_models)
    now = datetime.now(SEOUL).replace(microsecond=0).isoformat()
    return {
        "schemaVersion": 1,
        "source": {
            "name": "무공해차 통합누리집",
            "paymentUrl": PAYMENT_URL,
            "priceUrl": PRICE_URL,
        },
        "checkedAt": now,
        "year": datetime.now(SEOUL).year,
        "vehicleType": "전기승용",
        "modelStatus": {
            "name": "Volvo ES90",
            "officiallyListed": officially_listed,
            "officialModels": official_models,
            "message": (
                "ES90 공식 모델별 보조금이 반영되었습니다."
                if officially_listed
                else "ES90은 현재 공식 모델별 보조금 목록에 미등록되어, 금액은 50% 최대 예상액으로 표시합니다."
            ),
        },
        "regions": regions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payment-html", type=Path, required=True)
    parser.add_argument("--price-html", type=Path, required=True)
    parser.add_argument("--model-html", type=Path)
    parser.add_argument("--output", type=Path, default=Path("subsidy-data.json"))
    args = parser.parse_args()
    snapshot = build_snapshot(args.payment_html, args.price_html, args.model_html)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(snapshot['regions'])} regions to {args.output}")


if __name__ == "__main__":
    main()
