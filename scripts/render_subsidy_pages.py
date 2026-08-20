#!/usr/bin/env python3
"""Render the protected EV portal pages and save parseable official data.

The portal changed from captioned HTML tables to client-side AG Grid and
accordion views in August 2026.  This renderer supports both layouts.  For the
new payment view it walks every client-side page and writes a small synthetic
table that keeps the downstream parser independent from AG Grid internals.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from html import escape
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


PAYMENT_URL = "https://ev.or.kr/nportal/buySupprt/initSubsidyPaymentCheckAction.do"
PRICE_URL = "https://ev.or.kr/nportal/buySupprt/initPsLocalCarPirceAction.do"
MODEL_URL = (
    "https://ev.or.kr/nportal/buySupprt/psPopupLocalCarModelPrice.do?"
    "year=2026&local_cd=1100&local_nm=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C&car_type=11"
)

LEGACY_PAYMENT_TITLE = "지자체별 무공해차 구매보조금 지급현황"
NEW_PAYMENT_TITLE = "지자체별 보조금 현황"
LEGACY_PRICE_TITLE = "전기자동차 지자체 차종별 보조금 목록"
NEW_PRICE_TITLE = "지자체별 차종·모델 보조금"
LEGACY_MODEL_TITLE = "전기자동차 모델별 보조금 목록"

PRICE_REGIONS = (
    "서울특별시",
    "부산광역시",
    "대구광역시",
    "인천광역시",
    "광주광역시",
    "대전광역시",
    "울산광역시",
    "세종특별자치시",
)

BREAKDOWN_REGIONS = set(PRICE_REGIONS)


def first_int(value: str) -> int:
    match = re.search(r"-?[\d,]+", value or "")
    return int(match.group(0).replace(",", "")) if match else 0


def new_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"
    for argument in (
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-features=MediaRouter",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--window-size=1440,1200",
    ):
        options.add_argument(argument)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def body_text(driver: webdriver.Chrome) -> str:
    return driver.execute_script(
        "return document.body ? document.body.innerText : ''"
    )


def open_rendered_page(
    url: str,
    expected_texts: Iterable[str],
    *,
    attempts: int = 3,
    wait_seconds: int = 75,
) -> webdriver.Chrome:
    expected = tuple(expected_texts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        driver = None
        try:
            driver = new_driver()
            driver.set_page_load_timeout(60)
            driver.set_script_timeout(30)
            try:
                driver.get(url)
            except TimeoutException:
                # The portal keeps background connections alive. The table can
                # still be ready, so continue to the explicit DOM wait below.
                pass
            WebDriverWait(driver, wait_seconds, poll_frequency=0.5).until(
                lambda current: any(text in body_text(current) for text in expected)
            )
            return driver
        except (TimeoutException, WebDriverException, RuntimeError) as error:
            last_error = error
            print(f"Official page render retry {attempt}/{attempts}: {error}")
            time.sleep(attempt * 3)
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass
    raise RuntimeError(
        "공식 페이지 렌더링 실패: " + " / ".join(expected)
    ) from last_error


def category_text(row: dict, array_key: str, total_key: str) -> str:
    values = row.get(array_key)
    if isinstance(values, list) and values:
        return " ".join(str(value) for value in values)
    return str(row.get(total_key, ""))


def collect_visible_selection_breakdowns(
    driver: webdriver.Chrome,
    breakdowns: dict[str, dict],
) -> None:
    detail_buttons = driver.find_elements(
        By.XPATH,
        "//span[normalize-space()='상세보기']/ancestor::button[1]",
    )
    for button in detail_buttons:
        row = button.find_element(By.XPATH, "ancestor::*[@role='row'][1]")
        row_text = row.text
        region_name = next(
            (
                region
                for region in PRICE_REGIONS
                if region not in breakdowns and region in row_text
            ),
            None,
        )
        if not region_name:
            continue

        driver.execute_script("arguments[0].click()", button)
        WebDriverWait(driver, 20, poll_frequency=0.2).until(
            lambda current, name=region_name: current.execute_script(
                """
                const modal = document.querySelector('.modal-container.is-active');
                return Boolean(
                  modal
                  && modal.innerText.includes(arguments[0])
                  && modal.querySelectorAll('#myGrid3 [role="row"][row-index]').length
                );
                """,
                name,
            )
        )
        detail_rows = driver.find_elements(
            By.CSS_SELECTOR,
            ".modal-container.is-active #myGrid3 [role='row'][row-index]",
        )
        selected_by_category: dict[str, int] = {}
        for detail_row in detail_rows:
            category = detail_row.find_element(
                By.CSS_SELECTOR, "[role='gridcell'][col-id='category']"
            ).text.strip()
            selected = detail_row.find_element(
                By.CSS_SELECTOR, "[role='gridcell'][col-id='choice']"
            ).text.strip()
            selected_by_category[category] = first_int(selected)

        breakdown = {
            "general": selected_by_category.get("일반", 0),
            "priority": selected_by_category.get("우선순위", 0),
            "taxi": selected_by_category.get("택시", 0),
            "corporate": selected_by_category.get("법인·기관", 0),
            "total": selected_by_category.get("전체", 0),
        }
        if sum(
            breakdown[key]
            for key in ("general", "priority", "taxi", "corporate")
        ) != breakdown["total"]:
            raise RuntimeError(f"선정 세부 합계 불일치: {region_name}: {breakdown}")
        breakdowns[region_name] = breakdown
        driver.find_element(
            By.CSS_SELECTOR,
            ".modal-container.is-active .js-detail-close",
        ).click()
        WebDriverWait(driver, 10, poll_frequency=0.1).until(
            lambda current: not current.find_elements(
                By.CSS_SELECTOR, ".modal-container.is-active"
            )
        )


def collect_new_payment_rows(
    driver: webdriver.Chrome,
    breakdowns: dict[str, dict] | None = None,
) -> list[dict]:
    WebDriverWait(driver, 30, poll_frequency=0.25).until(
        lambda current: current.execute_script(
            """
            const root = document.querySelector('.ag-root-wrapper[grid-id]');
            return Boolean(root && window.agGrid && window.agGrid.getGridApi);
            """
        )
    )
    rows_by_key: dict[tuple[str, str], dict] = {}
    seen_pages: set[tuple] = set()

    # Do not rely on the visible page counter.  In headless Chrome the portal
    # can expose the AG Grid before its counter gets non-empty text.  Instead,
    # walk until the next button is disabled or the grid no longer advances.
    for page_number in range(1, 101):
        page_rows = driver.execute_script(
            """
            const root = document.querySelector('.ag-root-wrapper[grid-id]');
            const api = root && window.agGrid.getGridApi(root.getAttribute('grid-id'));
            const rows = [];
            if (api) api.forEachNode(node => rows.push(node.data));
            return rows;
            """
        )
        if not page_rows:
            raise RuntimeError(f"공식 보조금 그리드 {page_number}페이지가 비어 있습니다.")
        page_signature = tuple(
            (
                str(row.get("localCd", "")),
                str(row.get("carNm", "")),
                str(row.get("choice", "")),
                str(row.get("releaArr", "")),
                str(row.get("resiArr", "")),
            )
            for row in page_rows
        )
        if page_signature in seen_pages:
            break
        seen_pages.add(page_signature)

        for row in page_rows:
            if row.get("carNm") != "전기승용":
                continue
            key = (str(row.get("localCd", "")), str(row.get("carNm", "")))
            rows_by_key[key] = row

        if breakdowns is not None:
            collect_visible_selection_breakdowns(driver, breakdowns)

        next_button = driver.find_element(
            By.CSS_SELECTOR,
            ".pagination--fraction .pagination__button[data-page='next']",
        )
        next_disabled = driver.execute_script(
            """
            const button = arguments[0];
            return button.disabled
              || button.getAttribute('aria-disabled') === 'true'
              || button.classList.contains('disabled')
              || button.classList.contains('is-disabled');
            """,
            next_button,
        )
        if next_disabled:
            break

        driver.execute_script("arguments[0].click()", next_button)
        try:
            WebDriverWait(driver, 15, poll_frequency=0.1).until(
                lambda current, previous=page_signature: tuple(
                    (
                        str(row.get("localCd", "")),
                        str(row.get("carNm", "")),
                        str(row.get("choice", "")),
                        str(row.get("releaArr", "")),
                        str(row.get("resiArr", "")),
                    )
                    for row in current.execute_script(
                        """
                        const root = document.querySelector('.ag-root-wrapper[grid-id]');
                        const api = root && window.agGrid.getGridApi(root.getAttribute('grid-id'));
                        const rows = [];
                        if (api) api.forEachNode(node => rows.push(node.data));
                        return rows;
                        """
                    )
                )
                != previous
            )
        except TimeoutException:
            # The final page may keep an enabled-looking button while refusing
            # to advance.  A repeated signature is a deterministic end signal.
            repeated_rows = driver.execute_script(
                """
                const root = document.querySelector('.ag-root-wrapper[grid-id]');
                const api = root && window.agGrid.getGridApi(root.getAttribute('grid-id'));
                const rows = [];
                if (api) api.forEachNode(node => rows.push(node.data));
                return rows;
                """
            )
            repeated_signature = tuple(
                (
                    str(row.get("localCd", "")),
                    str(row.get("carNm", "")),
                    str(row.get("choice", "")),
                    str(row.get("releaArr", "")),
                    str(row.get("resiArr", "")),
                )
                for row in repeated_rows
            )
            if repeated_signature != page_signature:
                raise
            break

    rows = list(rows_by_key.values())
    if len(rows) < 150:
        raise RuntimeError(f"전국 전기승용 데이터가 불완전합니다: {len(rows)}개 지역")
    print(f"Collected {len(rows)} electric-passenger regions from {len(seen_pages)} pages")
    return rows


def write_payment_table(rows: list[dict], output: Path) -> None:
    headings = [
        "시도",
        "지역",
        "차종",
        "공고",
        "접수방법",
        "신청마감",
        "공고대수",
        "접수대수",
        "선정대수",
        "출고대수",
        "선정잔여대수",
        "출고잔여대수",
    ]
    rendered_rows = []
    for row in rows:
        labels = [str(file.get("label", "")) for file in row.get("files", [])]
        cells = [
            str(row.get("sido", "")),
            str(row.get("localNm", "")),
            str(row.get("carNm", "")),
            " / ".join(label for label in labels if label)
            or str(row.get("noticeKind", "")),
            str(row.get("accept", "")),
            str(row.get("deadline", "")),
            str(row.get("tcnt", "")),
            str(row.get("recei", "")),
            str(row.get("choice", "")),
            str(row.get("relea", "")),
            str(row.get("choiceRemain", "")),
            str(row.get("resi", "")),
        ]
        rendered_rows.append(
            "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>"
        )
    output.write_text(
        "<!doctype html><html><head><meta charset='utf-8'></head><body><table>"
        f"<caption>{LEGACY_PAYMENT_TITLE}</caption>"
        "<thead><tr>"
        + "".join(f"<th>{escape(heading)}</th>" for heading in headings)
        + "</tr></thead><tbody>"
        + "".join(rendered_rows)
        + "</tbody></table></body></html>",
        encoding="utf-8",
    )


def render_payment(output: Path, breakdown_output: Path | None = None) -> str:
    driver = open_rendered_page(
        PAYMENT_URL, (LEGACY_PAYMENT_TITLE, NEW_PAYMENT_TITLE)
    )
    try:
        rendered_text = body_text(driver)
        if LEGACY_PAYMENT_TITLE in rendered_text:
            output.write_text(driver.page_source, encoding="utf-8")
            mode = "legacy-table"
        else:
            breakdowns: dict[str, dict] = {}
            write_payment_table(
                collect_new_payment_rows(driver, breakdowns), output
            )
            missing = BREAKDOWN_REGIONS.difference(breakdowns)
            if missing:
                raise RuntimeError(
                    "선정 세부 데이터 누락: " + ", ".join(sorted(missing))
                )
            if breakdown_output is not None:
                breakdown_output.write_text(
                    json.dumps(breakdowns, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            mode = "ag-grid-all-pages"
        print(f"Rendered payment data ({mode}) -> {output}")
        return mode
    finally:
        driver.quit()


def expand_price_regions(driver: webdriver.Chrome) -> int:
    driver.execute_script(
        """
        const confirm = document.querySelector(
          '#alertCalcNotice .js-calc-notice-confirm'
        );
        if (confirm) confirm.click();
        """
    )
    expanded = 0
    for region_name in PRICE_REGIONS:
        item = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('.accordion-item')).find(
              item => item.querySelector('.location__district')?.innerText.trim()
                === arguments[0]
            ) || null;
            """,
            region_name,
        )
        if item is None:
            print(f"Price region not found; preserving fallback: {region_name}")
            continue
        local_code = item.get_attribute("data-local-cd")
        try:
            button = item.find_element(By.CSS_SELECTOR, ".accordion-button")
            driver.execute_script("arguments[0].click()", button)
            WebDriverWait(driver, 30, poll_frequency=0.25).until(
                lambda current, code=local_code: len(
                    current.find_elements(
                        By.CSS_SELECTOR,
                        f"#carGrid_{code} [role='row'][row-index]",
                    )
                )
                > 0
            )
            expanded += 1
        except (TimeoutException, WebDriverException) as error:
            print(f"Price region render failed; preserving fallback: {region_name}: {error}")
    return expanded


def render_price(output: Path) -> str:
    driver = open_rendered_page(
        PRICE_URL, (LEGACY_PRICE_TITLE, NEW_PRICE_TITLE)
    )
    try:
        rendered_text = body_text(driver)
        if LEGACY_PRICE_TITLE in rendered_text:
            mode = "legacy-table"
        else:
            expanded = expand_price_regions(driver)
            mode = f"accordion-grid-{expanded}-regions"
        output.write_text(driver.page_source, encoding="utf-8")
        print(f"Rendered price data ({mode}) -> {output}")
        return mode
    finally:
        driver.quit()


def render_legacy_model_optional(output: Path) -> None:
    try:
        driver = open_rendered_page(
            MODEL_URL,
            (LEGACY_MODEL_TITLE,),
            attempts=1,
            wait_seconds=30,
        )
    except RuntimeError as error:
        output.write_text("", encoding="utf-8")
        print(f"Model page unavailable; preserving last known status: {error}")
        return
    try:
        output.write_text(driver.page_source, encoding="utf-8")
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payment-output", type=Path, required=True)
    parser.add_argument("--breakdown-output", type=Path)
    parser.add_argument("--price-output", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    args = parser.parse_args()
    render_payment(args.payment_output, args.breakdown_output)
    price_mode = render_price(args.price_output)
    if price_mode == "legacy-table":
        render_legacy_model_optional(args.model_output)
    else:
        # The new price view contains the Seoul model grid used by the parser.
        args.model_output.write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
