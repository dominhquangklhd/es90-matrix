import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.html"
STATE_PATH = ROOT / "automation" / "getcha-price-state.json"
GETCHA_URL = "https://web.getcha.kr/discount-price/1"
SEOUL = ZoneInfo("Asia/Seoul")

# App model/trim names are immutable keys. Only the Getcha-side labels on the
# right may be adjusted when Getcha changes its wording.
MATCHES = [
    ("현대", "현대 그랜저 하이브리드", "프리미엄", "Hyundai", "Premium"),
    ("현대", "현대 그랜저 하이브리드", "익스클루시브", "Hyundai", "Exclusive"),
    ("현대", "현대 그랜저 하이브리드", "캘리그래피", "Hyundai", "Calligraphy"),
    ("현대", "현대 그랜저 하이브리드", "캘리그래피 블랙잉크", "Hyundai", "Black Ink"),
    ("제네시스", "제네시스 일렉트리파이드 G80", "e-AWD", "Genesis", "EV"),
    ("아우디", "아우디 A6 e-트론", "퍼포먼스 S-라인 플러스", "Audi", "Performance S-line+"),
    ("BMW", "BMW i5", "eDrive 40 M 스포츠 P2", "BMW i5", "eDrive40 M Sport"),
    ("BMW", "BMW i5", "xDrive 40 M 스포츠 P2", "BMW i5", "xDrive40 M Sport"),
    ("BMW", "BMW i5", "xDrive 40 M 스포츠 프로 P2", "BMW i5", "xDrive40 M Sport Pro"),
    ("BMW", "BMW i5", "M60 xDrive 프로 P2", "BMW i5", "M60 xDrive Pro"),
    ("BMW", "BMW iX3", "50 xDrive M 스포츠", "BMW iX3", "50 xDrive M Sport"),
    ("BMW", "BMW iX3", "50 xDrive M 스포츠 프로", "BMW iX3", "50 xDrive M Sport Pro"),
    ("벤츠", "벤츠 EQE", "EQE 350 +", "Mercedes-Benz", "350+"),
    ("폴스타", "폴스타 폴스타 3", "리어 모터", "Polestar 3", "Rear motor"),
    ("폴스타", "폴스타 폴스타 3", "듀얼 모터", "Polestar 3", "Dual motor"),
    ("폴스타", "폴스타 폴스타 3", "퍼포먼스", "Polestar 3", "Performance"),
    ("폴스타", "폴스타 폴스타 4", "리어 모터", "Polestar 4", "Rear motor"),
    ("폴스타", "폴스타 폴스타 4", "듀얼 모터", "Polestar 4", "Dual motor"),
    ("테슬라", "테슬라 모델 S", "AWD", "Tesla Model S", "AWD"),
    ("테슬라", "테슬라 모델 S", "플래드", "Tesla Model S", "Plaid"),
    ("테슬라", "테슬라 모델 Y", "L AWD", "Tesla", "Model Y L"),
]


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def newest_section(sections: list[dict], model_label: str) -> dict:
    candidates = [
        section
        for section in sections
        if normalize(section["title"]).startswith(model_label)
    ]
    if not candidates:
        raise RuntimeError(f"Getcha model not found: {model_label}")

    def model_year(section: dict) -> int:
        match = re.search(r"\((\d{2})년식\)", section["title"])
        return int(match.group(1)) if match else -1

    return max(candidates, key=model_year)


def parse_exact_price(section_text: str, exact_trim: str) -> dict:
    pattern = re.compile(
        rf"(?:제조사판매\s+)?{re.escape(exact_trim)}"
        rf"\s*(?:\(\d{{4}}\.\d{{2}}\))?\s+"
        rf"(?P<msrp>[\d,]+)\s*만원\s+"
        rf"(?:"
        rf"(?P<discounted>[\d,]+)\s*만원\s+"
        rf"(?P<promo>[\d,]+)\s*만원\s+\d+(?:\.\d+)?%"
        rf"|"
        rf"(?P<benefit>[\d,]+)\s*만원\s+\d+(?:\.\d+)?%"
        rf"|-\s+-"
        rf")"
    )
    matches = list(pattern.finditer(section_text))
    if len(matches) != 1:
        raise RuntimeError(
            f"Exact Getcha trim price match failed: {exact_trim} "
            f"({len(matches)} matches); section={section_text[:1600]!r}"
        )

    match = matches[0]
    msrp = int(match.group("msrp").replace(",", "")) * 10_000
    if match.group("discounted"):
        discounted_price = (
            int(match.group("discounted").replace(",", "")) * 10_000
        )
        promo = int(match.group("promo").replace(",", "")) * 10_000
    elif match.group("benefit"):
        promo = int(match.group("benefit").replace(",", "")) * 10_000
        discounted_price = msrp - promo
    else:
        promo = 0
        discounted_price = msrp

    if discounted_price <= 0 or discounted_price > msrp or promo < 0:
        raise RuntimeError(
            f"Invalid Getcha prices: {exact_trim} / {section_text[:600]}"
        )
    return {
        "msrp": msrp,
        "promo": promo,
        "discountedPrice": discounted_price,
    }


def select_brand(page: Page, brand: str) -> None:
    pattern = re.compile(rf"^\s*{re.escape(brand)}\s*\d+종\s*$")
    for attempt in range(1, 4):
        button = page.get_by_role("button").filter(has_text=pattern)
        if button.count() != 1:
            raise RuntimeError(f"Getcha brand button is not unique: {brand}")
        button.scroll_into_view_if_needed()
        button.evaluate("(element) => element.click()")
        try:
            page.wait_for_function(
                """expectedBrand => {
                  const heading = document.querySelector('main h1');
                  return heading && heading.innerText.trim() === expectedBrand;
                }""",
                arg=brand,
                timeout=10_000,
            )
            return
        except PlaywrightTimeoutError:
            if attempt == 3:
                raise RuntimeError(
                    f"Getcha brand page did not open after 3 attempts: {brand}"
                )


def read_sections(page: Page) -> list[dict]:
    return page.locator("h3").evaluate_all(
        """headings => headings.map(heading => {
          const tableWrap = heading.closest('a')?.nextElementSibling;
          return {
            title: (heading.innerText || '').trim().replace(/\\s+/g, ' '),
            text: (tableWrap?.innerText || '').trim().replace(/\\s+/g, ' ')
          };
        })"""
    )


def wait_for_brand_tables(page: Page, brand: str) -> None:
    expected_models = list(dict.fromkeys(
        item[1] for item in MATCHES if item[0] == brand
    ))
    for expected_model in expected_models:
        heading = page.locator("h3").filter(
            has_text=re.compile(rf"^\s*{re.escape(expected_model)}")
        )
        if heading.count() < 1:
            raise RuntimeError(
                f"Getcha model heading did not load: {brand} / {expected_model}"
            )
        heading.first.scroll_into_view_if_needed()
        try:
            page.wait_for_function(
                """expectedModel => {
                  const heading = Array.from(document.querySelectorAll('h3')).find(item =>
                    (item.innerText || '').trim().startsWith(expectedModel)
                  );
                  const tableWrap = heading?.closest('a')?.nextElementSibling;
                  return tableWrap && /[\\d,]+\\s*만원/.test(tableWrap.innerText || '');
                }""",
                arg=expected_model,
                timeout=30_000,
            )
        except PlaywrightTimeoutError as error:
            context = heading.first.evaluate(
                """item => {
                  const link = item.closest('a');
                  const parent = link?.parentElement;
                  return {
                    heading: (item.innerText || '').trim(),
                    linkTag: link?.tagName || '',
                    nextTag: link?.nextElementSibling?.tagName || '',
                    parentText: (parent?.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 1200),
                    tableCount: parent?.querySelectorAll('table').length || 0,
                    rowCount: parent?.querySelectorAll('tr').length || 0
                  };
                }"""
            )
            raise RuntimeError(
                f"Getcha price table did not load: {brand} / {expected_model}; "
                f"context={json.dumps(context, ensure_ascii=False)}"
            ) from error


def read_getcha_prices() -> tuple[dict[tuple[str, str], dict], list[str]]:
    collected: dict[tuple[str, str], dict] = {}
    unmatched: list[str] = []
    brands = list(dict.fromkeys(item[0] for item in MATCHES))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1100})
        page.goto(GETCHA_URL, wait_until="domcontentloaded", timeout=60_000)
        page.get_by_role("heading", name="BMW", exact=True).wait_for(
            state="visible", timeout=30_000
        )

        for brand in brands:
            brand_matches = [
                item for item in MATCHES if item[0] == brand
            ]
            try:
                select_brand(page, brand)
                wait_for_brand_tables(page, brand)
                sections = read_sections(page)
            except Exception as error:
                for _, _, _, app_model, app_trim in brand_matches:
                    unmatched.append(
                        f"{app_model} / {app_trim}: brand read failed ({error})"
                    )
                continue

            for (
                mapping_brand,
                getcha_model,
                getcha_trim,
                app_model,
                app_trim,
            ) in brand_matches:
                try:
                    section = newest_section(sections, getcha_model)
                    collected[(app_model, app_trim)] = parse_exact_price(
                        section["text"], getcha_trim
                    )
                except Exception as error:
                    unmatched.append(
                        f"{app_model} / {app_trim}: exact match failed ({error})"
                    )
        browser.close()

    if not collected:
        raise RuntimeError(
            "No Getcha trims were matched; all previous prices and dates preserved"
        )
    return collected, unmatched


def update_app(
    prices: dict[tuple[str, str], dict], unmatched: list[str]
) -> tuple[list[str], str]:
    app = APP_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"window\.ES90_DATA\s*=\s*(\{[\s\S]*?\});</script>", app
    )
    if not match:
        raise RuntimeError("window.ES90_DATA was not found in app.html")

    data = json.loads(match.group(1))
    checked_at = datetime.now(SEOUL).date().isoformat()
    changed_prices: list[str] = []

    for (model, trim), observed in prices.items():
        target = data["prices"].get(model, {}).get(trim)
        if target is None:
            raise RuntimeError(f"App trim key missing: {model} / {trim}")
        old_values = (
            int(target["msrp"]),
            int(target.get("promo", 0)),
            int(target.get("discountedPrice", target["msrp"])),
        )
        new_values = (
            observed["msrp"],
            observed["promo"],
            observed["discountedPrice"],
        )
        if old_values != new_values:
            changed_prices.append(f"{model} / {trim}")
            target["msrp"] = observed["msrp"]
            target["promo"] = observed["promo"]
            target["total"] = observed["msrp"]
            target["discountedPrice"] = observed["discountedPrice"]
        target["promotionSource"] = "Getcha"
        target["promotionCheckedAt"] = checked_at
        target["getchaMatchKey"] = f"{model} / {trim}"

    replacement = "window.ES90_DATA = " + json.dumps(
        data, ensure_ascii=False, indent=2
    ) + ";</script>"
    updated_app = app[: match.start()] + replacement + app[match.end() :]
    APP_PATH.write_text(updated_app, encoding="utf-8")

    state = {
        "checkedAt": datetime.now(SEOUL).isoformat(timespec="seconds"),
        "matchedTrimCount": len(prices),
        "unmatchedTrimCount": len(unmatched),
        "unmatchedTrims": unmatched,
        "priceChangedTrimCount": len(changed_prices),
        "priceChangedTrims": changed_prices,
    }
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return changed_prices, checked_at


def main() -> int:
    try:
        prices, unmatched = read_getcha_prices()
        changed_prices, checked_at = update_app(prices, unmatched)
        print(
            f"Getcha weekly check complete: {len(prices)} exact trims, "
            f"{len(unmatched)} preserved unmatched trims, "
            f"{len(changed_prices)} price changes, checked {checked_at}"
        )
        for trim in changed_prices:
            print(f"- changed: {trim}")
        for trim in unmatched:
            print(f"- preserved: {trim}")
        return 0
    except Exception as error:
        print(f"Getcha weekly check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
