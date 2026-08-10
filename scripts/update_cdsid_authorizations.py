import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.html"
LOGIN_URL = "https://sales.volvocars.kr/login/login.asp"
START_DATE = "260722"
TARGET_ROLES = ("영업직원", "영업팀장", "스페셜리스트")
AUTH_BLOCK_PATTERN = re.compile(
    r"const AUTHORIZED_CDSID_HASHES = new Set\(\[(.*?)\]\);",
    re.DOTALL,
)
HASH_PATTERN = re.compile(r"'([a-f0-9]{64})'")


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 GitHub Secret이 없습니다: {name}")
    return value


def normalize_cdsid(value: str) -> str:
    return str(value or "").strip().upper()


def hash_cdsid(value: str) -> str:
    normalized = normalize_cdsid(value)
    if not normalized:
        raise ValueError("빈 CDSID는 해시할 수 없습니다.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def today_yymmdd() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%y%m%d")


Frame = Any
Locator = Any
Page = Any


def visible(locator: Locator) -> bool:
    try:
        return locator.is_visible()
    except Exception:
        return False


def text_in_frames(page: Page, text: str) -> tuple[Frame, Locator] | None:
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    for frame in page.frames:
        matches = frame.get_by_text(pattern)
        for index in range(matches.count()):
            match = matches.nth(index)
            if visible(match):
                return frame, match
    return None


def click_text_in_frames(page: Page, text: str, *, required: bool = True) -> bool:
    found = text_in_frames(page, text)
    if found:
        _, match = found
        match.click(timeout=15_000)
        page.wait_for_timeout(500)
        return True
    if required:
        raise RuntimeError(f"Sales-DMS 메뉴를 찾지 못했습니다: {text}")
    return False


def frame_with_employee_grid(page: Page) -> Frame | None:
    for frame in page.frames:
        body_text = frame.locator("body").inner_text(timeout=5_000)
        if "직원 목록" in body_text and "직원 CDSID" in body_text:
            return frame
    return None


def login(page: Page, user_id: str, password: str) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    textboxes = page.get_by_role("textbox")
    if textboxes.count() != 2:
        raise RuntimeError("Sales-DMS 로그인 입력창 구조가 변경되었습니다.")
    textboxes.nth(0).fill(user_id)
    textboxes.nth(1).fill(password)
    login_button = page.get_by_role("button", name="Login", exact=True)
    if login_button.count() != 1:
        raise RuntimeError("Sales-DMS 로그인 버튼을 찾지 못했습니다.")
    login_button.click()
    page.wait_for_load_state("domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3_000)
    if "/login/" in page.url.lower():
        raise RuntimeError("Sales-DMS 로그인에 실패했습니다. GitHub Secret을 확인해 주세요.")


def open_employee_registration(page: Page) -> Frame:
    existing = frame_with_employee_grid(page)
    if existing:
        return existing

    for menu_text in (
        "Master data management",
        "Master 관리",
        "사용자 등록",
        "직원등록",
    ):
        click_text_in_frames(page, menu_text)

    deadline = datetime.now().timestamp() + 30
    while datetime.now().timestamp() < deadline:
        frame = frame_with_employee_grid(page)
        if frame:
            return frame
        page.wait_for_timeout(500)
    raise RuntimeError("직원등록 화면이 열리지 않았습니다.")


def row_containing(frame: Frame, label: str) -> Locator:
    normalized_label = re.sub(r"\s", "", label).lower()
    rows = frame.locator("tr")
    for index in range(rows.count()):
        row = rows.nth(index)
        if not visible(row):
            continue
        normalized_text = re.sub(r"\s", "", row.inner_text(timeout=5_000)).lower()
        if normalized_label in normalized_text:
            return row
    raise RuntimeError(f"직원등록 검색조건을 찾지 못했습니다: {label}")


def row_containing_in_frames(page: Page, label: str) -> tuple[Frame, Locator]:
    for frame in page.frames:
        try:
            return frame, row_containing(frame, label)
        except RuntimeError:
            continue
    raise RuntimeError(f"직원등록 검색조건을 찾지 못했습니다: {label}")


def choose_dropdown_value(page: Page, frame: Frame, row: Locator, value: str) -> None:
    selects = row.locator("select")
    for index in range(selects.count()):
        select = selects.nth(index)
        options = select.locator("option").all_text_contents(timeout=5_000)
        if any(option.strip() == value for option in options):
            select.select_option(label=value)
            return

    inputs = row.locator("input:not([type='hidden']):not([disabled])")
    for index in reversed(range(inputs.count())):
        input_box = inputs.nth(index)
        if not visible(input_box):
            continue
        input_box.click(timeout=10_000)
        page.wait_for_timeout(250)
        found = text_in_frames(page, value)
        if found:
            _, option = found
            option.click(timeout=10_000)
            page.wait_for_timeout(250)
            return
    raise RuntimeError(f"직원등록 드롭다운 값을 선택하지 못했습니다: {value}")


def configure_date_range(page: Page, start_date: str, end_date: str) -> None:
    frame, period_row = row_containing_in_frames(page, "기간조회")
    choose_dropdown_value(page, frame, period_row, "입사일자")
    inputs = period_row.locator(
        "input:not([type='hidden']):not([type='button']):not([disabled])"
    )
    visible_inputs = [inputs.nth(index) for index in range(inputs.count()) if visible(inputs.nth(index))]
    if len(visible_inputs) < 2:
        raise RuntimeError("입사일자 시작일·종료일 입력창을 찾지 못했습니다.")
    visible_inputs[-2].fill(start_date)
    visible_inputs[-1].fill(end_date)


def set_page_size(page: Page) -> None:
    for frame in page.frames:
        label = frame.get_by_text(re.compile(r"리스트\s*갯수", re.IGNORECASE))
        if not label.count():
            continue
        container = label.first.locator("xpath=parent::*")
        input_box = container.locator("input:not([type='hidden'])")
        if input_box.count() and visible(input_box.first):
            input_box.first.fill("500")
            input_box.first.press("Enter")
            return


def click_search(page: Page) -> None:
    for frame in page.frames:
        searches = frame.get_by_text("검색", exact=True)
        for index in range(searches.count()):
            search = searches.nth(index)
            if visible(search):
                search.click(timeout=10_000)
                page.wait_for_timeout(1_000)
                return
    raise RuntimeError("직원등록 검색 버튼을 찾지 못했습니다.")


def normalize_date_text(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 8 and digits.startswith("20"):
        return digits[2:]
    return digits


def extract_grid_rows(frame: Frame, expected_role: str) -> list[str]:
    rows = frame.locator("tr").evaluate_all(
        """rows => rows.map(row =>
            Array.from(row.querySelectorAll('th,td')).map(cell =>
                (cell.textContent || '').trim().replace(/\\s+/g, ' ')
            )
        )"""
    )
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if "직원 CDSID" in row and "직원권한" in row and "입사일자" in row
        ),
        -1,
    )
    if header_index < 0:
        body_text = frame.locator("body").inner_text(timeout=5_000)
        if any(message in body_text for message in ("조회 결과가 없습니다", "검색 결과가 없습니다")):
            return []
        raise RuntimeError("직원 목록 표의 열 구조가 변경되었습니다.")

    header = rows[header_index]
    role_index = header.index("직원권한")
    cdsid_index = header.index("직원 CDSID")
    hire_date_index = header.index("입사일자")
    max_index = max(role_index, cdsid_index, hire_date_index)
    results: list[str] = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max_index:
            continue
        role = row[role_index].strip()
        cdsid = normalize_cdsid(row[cdsid_index])
        hire_date = normalize_date_text(row[hire_date_index])
        if role == expected_role and cdsid and START_DATE <= hire_date <= today_yymmdd():
            results.append(cdsid)
    return results


def collect_recent_cdsids(user_id: str, password: str) -> tuple[set[str], dict[str, int]]:
    from playwright.sync_api import sync_playwright

    end_date = today_yymmdd()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            login(page, user_id, password)
            frame = open_employee_registration(page)
            set_page_size(page)
            configure_date_range(page, START_DATE, end_date)
            role_frame, role_row = row_containing_in_frames(page, "직원권한")
            collected: set[str] = set()
            role_counts: dict[str, int] = {}
            for role in TARGET_ROLES:
                choose_dropdown_value(page, role_frame, role_row, role)
                click_search(page)
                frame = frame_with_employee_grid(page) or frame
                role_rows = extract_grid_rows(frame, role)
                role_counts[role] = len(role_rows)
                collected.update(role_rows)
            return collected, role_counts
        finally:
            browser.close()


def read_authorized_hashes(app_text: str) -> list[str]:
    match = AUTH_BLOCK_PATTERN.search(app_text)
    if not match:
        raise RuntimeError("app.html의 AUTHORIZED_CDSID_HASHES 영역을 찾지 못했습니다.")
    hashes = HASH_PATTERN.findall(match.group(1))
    if not hashes:
        raise RuntimeError("app.html의 CDSID 허용 해시 목록이 비어 있습니다.")
    if len(hashes) != len(set(hashes)):
        raise RuntimeError("app.html의 CDSID 허용 해시 목록에 중복이 있습니다.")
    return hashes


def append_authorized_hashes(app_text: str, new_hashes: set[str]) -> str:
    existing_hashes = read_authorized_hashes(app_text)
    additions = sorted(new_hashes - set(existing_hashes))
    if not additions:
        return app_text
    all_hashes = existing_hashes + additions
    replacement = (
        "const AUTHORIZED_CDSID_HASHES = new Set([\n"
        + ",\n".join(f"  '{value}'" for value in all_hashes)
        + "\n]);"
    )
    updated, substitutions = AUTH_BLOCK_PATTERN.subn(replacement, app_text, count=1)
    if substitutions != 1:
        raise RuntimeError("app.html의 CDSID 허용 해시 목록을 갱신하지 못했습니다.")
    return updated


def write_github_output(changed: bool, scanned_count: int, new_count: int) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"changed={'true' if changed else 'false'}\n")
        output.write(f"scanned_count={scanned_count}\n")
        output.write(f"new_count={new_count}\n")


def main() -> int:
    try:
        user_id = required_env("VOLVO_SALES_ID")
        password = required_env("VOLVO_SALES_PASSWORD")
        cdsids, role_counts = collect_recent_cdsids(user_id, password)
        app_text = APP_PATH.read_text(encoding="utf-8")
        existing_hashes = set(read_authorized_hashes(app_text))
        scanned_hashes = {hash_cdsid(cdsid) for cdsid in cdsids}
        new_hashes = scanned_hashes - existing_hashes
        updated_app = append_authorized_hashes(app_text, new_hashes)
        changed = updated_app != app_text
        if changed:
            APP_PATH.write_text(updated_app, encoding="utf-8")
        write_github_output(changed, len(scanned_hashes), len(new_hashes))
        role_summary = ", ".join(f"{role} {role_counts.get(role, 0)}명" for role in TARGET_ROLES)
        print(
            f"Sales-DMS 신규 입사자 확인 완료: {role_summary}, "
            f"중복 제거 {len(scanned_hashes)}명, 신규 권한 {len(new_hashes)}명"
        )
        return 0
    except Exception as error:
        print(f"CDSID 자동 갱신 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
