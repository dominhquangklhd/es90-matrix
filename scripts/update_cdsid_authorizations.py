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
START_DATE = "260811"
TARGET_ROLES = (
    "영업직원",
    "영업팀장",
    "스페셜리스트",
    "세일즈 본부장",
    "세일즈 지점장",
)
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


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


Frame = Any
Locator = Any
Page = Any


def visible(locator: Locator) -> bool:
    try:
        return locator.is_visible()
    except Exception:
        return False


def text_in_frames(page: Page, text: str) -> tuple[Page, Frame, Locator] | None:
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    for candidate_page in reversed(page.context.pages):
        for frame in candidate_page.frames:
            matches = frame.get_by_text(pattern)
            for index in range(matches.count()):
                match = matches.nth(index)
                if visible(match):
                    return candidate_page, frame, match
    return None


def click_text_in_frames(page: Page, text: str, *, required: bool = True) -> bool:
    found = text_in_frames(page, text)
    if found:
        target_page, _, match = found
        href = match.get_attribute("href") or ""
        if href:
            with target_page.expect_navigation(
                wait_until="domcontentloaded", timeout=60_000
            ):
                match.click(timeout=15_000)
        else:
            match.click(timeout=15_000)
        target_page.wait_for_timeout(1_000)
        return True
    if required:
        raise RuntimeError(f"Sales-DMS 메뉴를 찾지 못했습니다: {text}")
    return False


def employee_grid_in_context(page: Page) -> tuple[Page, Frame] | None:
    for candidate_page in reversed(page.context.pages):
        for frame in candidate_page.frames:
            body_text = frame.locator("body").inner_text(timeout=5_000)
            page_controls_ready = (
                frame.locator("#s_empt_id").count() > 0
                and frame.locator("#com_cd").count() > 0
            )
            if (
                page_controls_ready
                and "직원 목록" in body_text
                and "직원 CDSID" in body_text
            ):
                return candidate_page, frame
    return None


def wait_for_employee_grid(page: Page, timeout_ms: int = 30_000) -> tuple[Page, Frame]:
    deadline = datetime.now().timestamp() + (timeout_ms / 1_000)
    while datetime.now().timestamp() < deadline:
        employee_grid = employee_grid_in_context(page)
        if employee_grid:
            return employee_grid
        page.wait_for_timeout(500)
    raise RuntimeError("직원등록 검색 제어가 로드되지 않습니다.")


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


def open_employee_registration(page: Page) -> tuple[Page, Frame]:
    existing = employee_grid_in_context(page)
    if existing:
        return existing

    for menu_text in (
        "Master data management",
        "Master 관리",
        "사용자 등록",
        "직원등록",
    ):
        click_text_in_frames(page, menu_text)

    return wait_for_employee_grid(page)


def configure_date_range(
    page: Page,
    date_label: str,
    employment_status: str,
    start_date: str,
    end_date: str,
) -> None:
    _, frame = wait_for_employee_grid(page)
    date_type = frame.locator("#date_type")
    status_type = frame.locator("select[name='resign_type']")
    start_input = frame.locator("#s_date")
    end_input = frame.locator("#e_date")
    if not (
        date_type.count()
        and status_type.count()
        and start_input.count()
        and end_input.count()
    ):
        raise RuntimeError(f"{date_label} 검색 조건을 찾지 못했습니다.")
    date_type.select_option(label=date_label)
    status_type.select_option(label=employment_status)
    start_input.fill(start_date)
    end_input.fill(end_date)


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
            wait_for_employee_grid(page)
            return


def click_search(page: Page) -> tuple[Page, Frame]:
    target_page, frame = wait_for_employee_grid(page)
    search = frame.locator("#search")
    if not search.count() or not visible(search):
        raise RuntimeError("직원등록 검색 버튼을 찾지 못했습니다.")
    search.click(timeout=10_000)
    target_page.wait_for_timeout(1_000)
    return wait_for_employee_grid(page)


def normalize_date_text(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 8 and digits.startswith("20"):
        return digits[2:]
    return digits


def extract_grid_rows(
    frame: Frame,
    expected_role: str,
    date_label: str,
    start_date: str,
    end_date: str,
) -> dict[str, str]:
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
            if "직원 CDSID" in row and "직원권한" in row and date_label in row
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
    date_index = header.index(date_label)
    max_index = max(role_index, cdsid_index, date_index)
    results: dict[str, str] = {}
    for row in rows[header_index + 1 :]:
        if len(row) <= max_index:
            continue
        role = row[role_index].strip()
        cdsid = normalize_cdsid(row[cdsid_index])
        employee_date = normalize_date_text(row[date_index])
        if role == expected_role and cdsid and start_date <= employee_date <= end_date:
            results[cdsid] = max(results.get(cdsid, ""), employee_date)
    return results


def collect_role_cdsids(
    page: Page,
    date_label: str,
    employment_status: str,
    end_date: str,
) -> tuple[dict[str, str], dict[str, int]]:
    configure_date_range(
        page,
        date_label,
        employment_status,
        START_DATE,
        end_date,
    )
    collected: dict[str, str] = {}
    role_counts: dict[str, int] = {}
    for role in TARGET_ROLES:
        page, frame = wait_for_employee_grid(page)
        role_select = frame.locator("#com_cd")
        if not role_select.count():
            raise RuntimeError("직원권한 선택창을 찾지 못했습니다.")
        role_select.select_option(label=role)
        page, frame = click_search(page)
        role_rows = extract_grid_rows(
            frame,
            role,
            date_label,
            START_DATE,
            end_date,
        )
        role_counts[role] = len(role_rows)
        for cdsid, event_date in role_rows.items():
            collected[cdsid] = max(collected.get(cdsid, ""), event_date)
    return collected, role_counts


def resolve_access_events(
    hired_dates: dict[str, str],
    departed_dates: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Resolve access from the latest hire/departure event per CDSID.

    A returning employee can have both events inside the search range.  A hire
    on the same day as, or after, the latest departure restores access; only a
    strictly later departure revokes it.
    """

    all_cdsids = set(hired_dates) | set(departed_dates)
    active_hires: set[str] = set()
    final_departures: set[str] = set()
    for cdsid in all_cdsids:
        hire_date = hired_dates.get(cdsid, "")
        departure_date = departed_dates.get(cdsid, "")
        if hire_date and hire_date >= departure_date:
            active_hires.add(cdsid)
        elif departure_date:
            final_departures.add(cdsid)
    return active_hires, final_departures


def collect_access_changes(
    user_id: str,
    password: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, int], dict[str, int]]:
    from playwright.sync_api import sync_playwright

    end_date = today_yymmdd()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            login(page, user_id, password)
            page, frame = open_employee_registration(page)
            set_page_size(page)
            page, frame = wait_for_employee_grid(page)
            hired, hire_counts = collect_role_cdsids(
                page,
                "입사일자",
                "재직자",
                end_date,
            )
            departed, departure_counts = collect_role_cdsids(
                page,
                "퇴사일자",
                "퇴사자",
                end_date,
            )
            return hired, departed, hire_counts, departure_counts
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


def update_authorized_hashes(
    app_text: str,
    new_hashes: set[str],
    revoked_hashes: set[str],
) -> str:
    existing_hashes = read_authorized_hashes(app_text)
    existing_set = set(existing_hashes)
    additions = sorted(new_hashes - existing_set - revoked_hashes)
    retained = [value for value in existing_hashes if value not in revoked_hashes]
    all_hashes = retained + additions
    if all_hashes == existing_hashes:
        return app_text
    replacement = (
        "const AUTHORIZED_CDSID_HASHES = new Set([\n"
        + ",\n".join(f"  '{value}'" for value in all_hashes)
        + "\n]);"
    )
    updated, substitutions = AUTH_BLOCK_PATTERN.subn(replacement, app_text, count=1)
    if substitutions != 1:
        raise RuntimeError("app.html의 CDSID 허용 해시 목록을 갱신하지 못했습니다.")
    return updated


def append_authorized_hashes(app_text: str, new_hashes: set[str]) -> str:
    return update_authorized_hashes(app_text, new_hashes, set())


def write_github_output(
    changed: bool,
    scanned_count: int,
    new_count: int,
    departed_count: int = 0,
    revoked_count: int = 0,
    checked_at: str | None = None,
) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"changed={'true' if changed else 'false'}\n")
        output.write(f"scanned_count={scanned_count}\n")
        output.write(f"new_count={new_count}\n")
        output.write(f"departed_count={departed_count}\n")
        output.write(f"revoked_count={revoked_count}\n")
        output.write(f"checked_at={checked_at or now_iso()}\n")


def main() -> int:
    try:
        user_id = required_env("VOLVO_SALES_ID")
        password = required_env("VOLVO_SALES_PASSWORD")
        hired_dates, departed_dates, hire_counts, departure_counts = (
            collect_access_changes(user_id, password)
        )
        hired_cdsids, departed_cdsids = resolve_access_events(
            hired_dates,
            departed_dates,
        )
        app_text = APP_PATH.read_text(encoding="utf-8")
        existing_hashes = set(read_authorized_hashes(app_text))
        hired_hashes = {hash_cdsid(cdsid) for cdsid in hired_cdsids}
        departed_hashes = {hash_cdsid(cdsid) for cdsid in departed_cdsids}
        new_hashes = hired_hashes - existing_hashes - departed_hashes
        revoked_hashes = departed_hashes & existing_hashes
        updated_app = update_authorized_hashes(
            app_text,
            new_hashes,
            departed_hashes,
        )
        changed = updated_app != app_text
        if changed:
            APP_PATH.write_text(updated_app, encoding="utf-8")
        write_github_output(
            changed,
            len(hired_dates),
            len(new_hashes),
            len(departed_dates),
            len(revoked_hashes),
            now_iso(),
        )
        hire_summary = ", ".join(
            f"{role} {hire_counts.get(role, 0)}명" for role in TARGET_ROLES
        )
        departure_summary = ", ".join(
            f"{role} {departure_counts.get(role, 0)}명" for role in TARGET_ROLES
        )
        print(
            f"Sales-DMS 신규 입사자 확인 완료: {hire_summary}, "
            f"신규 권한 {len(new_hashes)}명"
        )
        print(
            f"Sales-DMS 퇴사자 확인 완료: {departure_summary}, "
            f"로그인 차단 {len(revoked_hashes)}명"
        )
        returning_count = len(set(hired_dates) & set(departed_dates) & hired_cdsids)
        print(f"재입사 우선 처리 완료: 로그인 유지 또는 복원 {returning_count}명")
        return 0
    except Exception as error:
        print(f"CDSID 자동 갱신 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
