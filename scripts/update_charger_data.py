#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "charger-data-350kw.json"
API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
MINIMUM_OUTPUT_KW = 350
PAGE_SIZE = 1000


def request_page(service_key: str, page_no: int) -> dict:
    query = urllib.parse.urlencode(
        {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": PAGE_SIZE,
            "dataType": "JSON",
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "ES90-Charger-Data-Updater/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def get_body(payload: dict) -> dict:
    body = payload.get("response", {}).get("body")
    if not isinstance(body, dict):
        raise RuntimeError("공공데이터포털 응답 형식을 확인할 수 없습니다.")
    return body


def fetch_all(service_key: str) -> list[dict]:
    first_body = get_body(request_page(service_key, 1))
    items = list(first_body.get("items", {}).get("item", []) or [])
    total_count = int(first_body.get("totalCount", len(items)) or 0)
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    for page_no in range(2, total_pages + 1):
        body = get_body(request_page(service_key, page_no))
        items.extend(body.get("items", {}).get("item", []) or [])
    return items


def number(value, default=0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").strip())
    except ValueError:
        return default


def text(item: dict, key: str) -> str:
    return str(item.get(key) or "").strip()


def category(output_kw: float) -> str:
    if output_kw <= 11:
        return "slow"
    if output_kw < 200:
        return "fast"
    if output_kw < 350:
        return "ultraFast"
    return "hyperFast"


def build_stations(items: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        station_id = text(item, "statId")
        if station_id and text(item, "delYn").upper() != "Y":
            grouped[station_id].append(item)

    stations = []
    for station_id, chargers in grouped.items():
        outputs = [number(item.get("output")) for item in chargers]
        maximum_output = max(outputs, default=0)
        if maximum_output < MINIMUM_OUTPUT_KW:
            continue

        first = chargers[0]
        counts = {"slow": 0, "fast": 0, "ultraFast": 0, "hyperFast": 0}
        for output_kw in outputs:
            counts[category(output_kw)] += 1

        lat = number(first.get("lat"), None)
        lng = number(first.get("lng"), None)
        if lat is None or lng is None:
            continue

        stations.append(
            {
                "id": station_id,
                "name": text(first, "statNm"),
                "address": text(first, "addr"),
                "lat": lat,
                "lng": lng,
                "power": int(maximum_output)
                if maximum_output.is_integer()
                else maximum_output,
                "chargers": counts["hyperFast"],
                "totalChargers": len(chargers),
                "chargerCounts": counts,
                "operator": text(first, "busiNm"),
                "useTime": text(first, "useTime"),
                "parkingFree": text(first, "parkingFree"),
                "kind": text(first, "kind"),
                "kindDetail": text(first, "kindDetail"),
            }
        )

    return sorted(stations, key=lambda station: (station["name"], station["id"]))


def comparable(payload: dict) -> dict:
    result = dict(payload)
    result.pop("updatedAt", None)
    return result


def main() -> int:
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not service_key:
        print("DATA_GO_KR_SERVICE_KEY가 설정되지 않았습니다.", file=sys.stderr)
        return 2

    stations = build_stations(fetch_all(service_key))
    if not stations:
        raise RuntimeError("350kW 이상 충전소가 한 곳도 없어 기존 파일을 보호합니다.")

    now = datetime.now(ZoneInfo("Asia/Seoul"))
    payload = {
        "source": "공공데이터포털 전기자동차 충전소 정보",
        "endpoint": "B552584/EvCharger/getChargerInfo",
        "updatedAt": now.isoformat(timespec="seconds"),
        "minimumOutputKw": MINIMUM_OUTPUT_KW,
        "categoryRules": {
            "slow": "11kW 이하",
            "fast": "11kW 초과 200kW 미만",
            "ultraFast": "200kW 이상 350kW 미만",
            "hyperFast": "350kW 이상",
        },
        "stationCount": len(stations),
        "stations": stations,
    }

    previous = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    if comparable(previous) == comparable(payload):
        print("충전소 데이터 변경 없음")
        return 0

    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"350kW 이상 충전소 {len(stations)}곳으로 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
