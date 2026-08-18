#!/usr/bin/env python3
"""Build the nationwide EV subsidy snapshot from rendered EV portal HTML.

The EV portal protects its HTML with client-side PNP rendering, so the workflow
first renders the official pages in headless Chrome and then passes the
resulting DOM files to this script.  Both the legacy table layout and the
August 2026 AG Grid/accordion layout are supported.
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


def class_xpath(class_name: str) -> str:
    return (
        "contains(concat(' ', normalize-space(@class), ' '), "
        f"' {class_name} ')"
    )


def clean_text(node) -> str:
    return re.sub(r"\s+", " ", " ".join(node.itertext())).strip()


def first_int(text: str) -> int:
    match = re.search(r"-?[\d,]+", text or "")
    return int(match.group(0).replace(",", "")) if match else 0


def general_passenger_int(text: str) -> int:
    """Return the general-passenger value from an EV portal allocation cell.

    The official payment table lists one total followed by four parenthesized
    categories in this order: priority, corporation/institution, taxi, general.
    ES90 retail consultations must use the final (general) category, not total.
    """
    categories = re.findall(r"\(\s*(-?[\d,]+)\s*\)", text or "")
    if len(categories) < 4:
        raise ValueError(f"일반 승용 구분값을 찾지 못했습니다: {text!r}")
    return int(categories[-1].replace(",", ""))


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


def load_fallback(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def fallback_prices(snapshot: dict | None) -> dict[tuple[str, str], int]:
    if not snapshot:
        return {}
    return {
        (str(region.get("sido", "")), str(region.get("sigungu", ""))): int(
            region.get("combinedMaxManwon", 0) or 0
        )
        for region in snapshot.get("regions", [])
    }


def new_price_data(path: Path) -> tuple[dict[tuple[str, str], int], list[dict], list[str]]:
    if not path.exists() or path.stat().st_size == 0:
        return {}, [], []
    root = html.parse(str(path))
    items = root.xpath(f"//div[{class_xpath('accordion-item')}]")
    prices: dict[tuple[str, str], int] = {}
    official_models: dict[str, dict] = {}
    live_regions: list[str] = []

    for item in items:
        district_nodes = item.xpath(f".//*[{class_xpath('location__district')}]")
        city_nodes = item.xpath(f".//*[{class_xpath('location__city')}]")
        if not district_nodes or not city_nodes:
            continue
        district = clean_text(district_nodes[0])
        city = clean_text(city_nodes[0])
        sido = SIDO_NAMES.get(city, city)
        max_local = 0
        passenger_rows = 0

        for row in item.xpath(".//*[@role='row'][@row-index]"):
            cells = {
                cell.get("col-id", ""): clean_text(cell)
                for cell in row.xpath(".//*[@role='gridcell']")
            }
            if not cells.get("carNm", "").startswith("전기승용"):
                continue
            passenger_rows += 1
            max_local = max(max_local, first_int(cells.get("local", "")))
            maker = cells.get("maker", "")
            model = cells.get("model", "")
            if "볼보" in maker and "ES90" in model.upper():
                official_models[model] = {
                    "modelName": model,
                    "nationalManwon": first_int(cells.get("gov", "")),
                    "seoulLocalManwon": first_int(cells.get("local", "")),
                    "seoulCombinedManwon": first_int(cells.get("total", "")),
                }

        if passenger_rows:
            prices[(sido, district)] = NATIONAL_MAX_MANWON + max_local
            live_regions.append(district)

    return prices, list(official_models.values()), live_regions


def build_snapshot(
    payment_html: Path,
    price_html: Path,
    model_html: Path | None = None,
    fallback_snapshot: dict | None = None,
) -> dict:
    payments = table_rows(payment_html, "지자체별 무공해차 구매보조금 지급현황")
    price_by_region = fallback_prices(fallback_snapshot)
    price_mode = "last-known-good"
    price_regions_live: list[str] = []
    official_models: list[dict] = []

    try:
        prices = table_rows(price_html, "전기자동차 지자체 차종별 보조금 목록")
    except RuntimeError:
        live_prices, official_models, price_regions_live = new_price_data(price_html)
        if live_prices:
            price_by_region.update(live_prices)
            price_mode = "live-selected-regions-with-last-known-good-fallback"
    else:
        price_by_region.update({
            (SIDO_NAMES.get(row[0], row[0]), row[1]): first_int(row[3])
            for row in prices
            if len(row) >= 4 and row[0] != "공단"
        })
        price_mode = "live-all-regions"

    if not price_by_region:
        raise RuntimeError("공식 금액 데이터와 마지막 정상 금액 데이터가 모두 없습니다.")

    regions = []
    for row in payments:
        if len(row) < 9 or row[0] == "공단":
            continue
        sido = SIDO_NAMES.get(row[0], row[0])
        combined = price_by_region.get((sido, row[1]), 0)
        announced = general_passenger_int(row[5])
        received = general_passenger_int(row[6])
        delivered = general_passenger_int(row[7])
        regions.append({
            "sido": sido,
            "sigungu": row[1],
            "announced": announced,
            "received": received,
            "delivered": delivered,
            "remaining": max(0, announced - delivered),
            "nationalMaxManwon": NATIONAL_MAX_MANWON,
            "localMaxManwon": max(0, combined - NATIONAL_MAX_MANWON),
            "combinedMaxManwon": combined,
            "applicationMethod": row[4].lstrip("*"),
            "notice": row[3],
        })

    if len(regions) < 150:
        raise RuntimeError(f"전국 데이터가 불완전합니다: {len(regions)}개 지역")

    if not official_models and model_html and model_html.exists() and model_html.stat().st_size:
        try:
            model_rows = table_rows(model_html, "전기자동차 모델별 보조금 목록")
        except RuntimeError:
            model_rows = []
        for row in model_rows:
            if len(row) >= 6 and "볼보" in row[1] and "ES90" in row[2].upper():
                official_models.append({
                    "modelName": row[2],
                    "nationalManwon": first_int(row[3]),
                    "seoulLocalManwon": first_int(row[4]),
                    "seoulCombinedManwon": first_int(row[5]),
                })

    fallback_model_status = (fallback_snapshot or {}).get("modelStatus", {})
    if official_models:
        model_status = {
            "name": "Volvo ES90",
            "officiallyListed": True,
            "officialModels": official_models,
            "message": "ES90 공식 모델별 보조금이 반영되었습니다.",
        }
        model_mode = "live"
    elif fallback_model_status:
        model_status = fallback_model_status
        model_mode = "last-known-good"
    else:
        model_status = {
            "name": "Volvo ES90",
            "officiallyListed": False,
            "officialModels": [],
            "message": "ES90은 현재 공식 모델별 보조금 목록에 미등록되어, 금액은 50% 최대 예상액으로 표시합니다.",
        }
        model_mode = "not-listed"

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
        "allocationBasis": "일반 승용",
        "collection": {
            "payment": "live-official",
            "prices": price_mode,
            "priceRegionsLive": price_regions_live,
            "model": model_mode,
        },
        "modelStatus": model_status,
        "regions": regions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payment-html", type=Path, required=True)
    parser.add_argument("--price-html", type=Path, required=True)
    parser.add_argument("--model-html", type=Path)
    parser.add_argument("--output", type=Path, default=Path("subsidy-data.json"))
    args = parser.parse_args()
    fallback_snapshot = load_fallback(args.output)
    snapshot = build_snapshot(
        args.payment_html,
        args.price_html,
        args.model_html,
        fallback_snapshot=fallback_snapshot,
    )
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(snapshot['regions'])} regions to {args.output}")


if __name__ == "__main__":
    main()
