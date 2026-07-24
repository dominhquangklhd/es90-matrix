import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.html"
STATE_PATH = ROOT / "automation" / "es90-reservation-state.json"
LOGIN_URL = "https://sales.volvocars.kr/login/login.asp"
REPORT_URL = "https://sales.volvocars.kr/09/10/01/report_v2.asp"

TRIM_MAP = {
    "Single Motor ER Plus": ("Single Motor Plus", None),
    "Single Motor ER Ult": ("Single Motor Ultra", True),
    "Single Motor ER Ult (No Air Sus)": ("Single Motor Ultra", False),
    "Twin Motor Performance Ult": ("Twin Motor Performance Ultra", None),
    "Twin Motor Plus": ("Twin Motor Plus", None),
    "Twin Motor Ult": ("Twin Motor Ultra", True),
    "Twin Motor Ult (No Air Sus)": ("Twin Motor Ultra", False),
}


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 GitHub Secret이 없습니다: {name}")
    return value


def read_report(user_id: str, password: str) -> tuple[int, list[dict]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

        textboxes = page.get_by_role("textbox")
        if textboxes.count() != 2:
            raise RuntimeError("로그인 입력창 구조가 예상과 다릅니다.")
        textboxes.nth(0).fill(user_id)
        textboxes.nth(1).fill(password)

        login_button = page.get_by_role("button", name="Login", exact=True)
        if login_button.count() != 1:
            raise RuntimeError("로그인 버튼을 찾지 못했습니다.")
        login_button.click()
        page.wait_for_load_state("domcontentloaded", timeout=60_000)

        if "/login/" in page.url.lower():
            raise RuntimeError("로그인에 실패했습니다. 아이디·비밀번호를 확인하세요.")

        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("tr", state="attached", timeout=60_000)

        rows = page.locator("tr").evaluate_all(
            """rows => rows.map(row =>
                Array.from(row.querySelectorAll('th,td')).map(cell =>
                    (cell.textContent || '').trim().replace(/\\s+/g, ' ')
                )
            )"""
        )
        browser.close()

    start = next(
        (
            index
            for index, row in enumerate(rows)
            if len(row) > 1 and row[0] == "ES90" and "Motor" in row[1]
        ),
        -1,
    )
    if start < 0:
        raise RuntimeError("Daily Report에서 ES90 트림 표를 찾지 못했습니다.")

    extracted = []
    for offset in range(7):
        row = rows[start + offset]
        source_trim = row[1] if offset == 0 else row[0]
        if source_trim not in TRIM_MAP:
            raise RuntimeError(f"예상하지 못한 ES90 트림입니다: {source_trim}")

        total_index = 6 if offset == 0 else 5
        raw_count = row[total_index].replace(",", "").strip()
        if not raw_count.isdigit():
            raise RuntimeError(f"{source_trim} 합계값을 읽지 못했습니다: {raw_count!r}")

        display_trim, air_suspension = TRIM_MAP[source_trim]
        item = {"trim": display_trim, "count": int(raw_count)}
        if air_suspension is not None:
            item["airSuspension"] = air_suspension
            if air_suspension is False:
                item["variant"] = "No Air Sus"
        extracted.append(item)

    total_row = rows[start + 7]
    if "ES90 Total" not in total_row[0]:
        raise RuntimeError("ES90 합계 행을 찾지 못했습니다.")
    report_total = int(total_row[5].replace(",", ""))
    calculated_total = sum(item["count"] for item in extracted)
    if report_total != calculated_total:
        raise RuntimeError(
            f"트림 합계({calculated_total})와 보고서 합계({report_total})가 다릅니다."
        )

    extracted.sort(key=lambda item: item["count"], reverse=True)
    return report_total, extracted


def js_item(item: dict) -> str:
    parts = [f"trim:'{item['trim']}'", f"count:{item['count']}"]
    if "airSuspension" in item:
        parts.append(
            f"airSuspension:{str(item['airSuspension']).lower()}"
        )
    if item.get("variant"):
        parts.append(f"variant:'{item['variant']}'")
    return "    {" + ", ".join(parts) + "}"


def update_app(total: int, trims: list[dict]) -> bool:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    previous_total = int(state["total"])
    previous_day_total = int(state["previousDayTotal"])

    if state["observationDate"] != now.date().isoformat():
        previous_day_total = previous_total

    new_state = {
        "observationDate": now.date().isoformat(),
        "observedAt": now.isoformat(timespec="seconds"),
        "total": total,
        "previousDayTotal": previous_day_total,
        "byTrim": trims,
    }

    data_changed = (
        total != previous_total
        or previous_day_total != int(state["previousDayTotal"])
        or trims != state["byTrim"]
    )
    if not data_changed:
        return False

    app = APP_PATH.read_text(encoding="utf-8")
    block_pattern = re.compile(
        r"const RESERVATION_DATA = \{\s*"
        r"total:\s*\d+,\s*"
        r"previousDayTotal:\s*\d+,\s*"
        r"byTrim:\s*\[.*?\]\s*"
        r"\};",
        re.DOTALL,
    )
    items = ",\n".join(js_item(item) for item in trims)
    replacement = (
        "const RESERVATION_DATA = {\n"
        f"  total: {total},\n"
        f"  previousDayTotal: {previous_day_total},\n"
        "  byTrim: [\n"
        f"{items}\n"
        "  ]\n"
        "};"
    )
    updated_app, substitutions = block_pattern.subn(replacement, app, count=1)
    if substitutions != 1:
        raise RuntimeError("app.html의 RESERVATION_DATA 영역을 찾지 못했습니다.")

    app_changed = updated_app != app
    STATE_PATH.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if app_changed:
        APP_PATH.write_text(updated_app, encoding="utf-8")
    return True


def main() -> int:
    try:
        user_id = required_env("VOLVO_SALES_ID")
        password = required_env("VOLVO_SALES_PASSWORD")
        total, trims = read_report(user_id, password)
        changed = update_app(total, trims)
        print(
            f"ES90 예약 데이터 확인 완료: {total}명"
            + (" (변경됨)" if changed else " (변경 없음)")
        )
        return 0
    except Exception as error:
        print(f"자동수집 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
