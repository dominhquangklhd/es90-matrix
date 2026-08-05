#!/usr/bin/env python3
"""Render the protected EV portal pages and save their post-JavaScript DOM."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait


PAYMENT_URL = "https://ev.or.kr/nportal/buySupprt/initSubsidyPaymentCheckAction.do"
PRICE_URL = "https://ev.or.kr/nportal/buySupprt/initPsLocalCarPirceAction.do"
MODEL_URL = (
    "https://ev.or.kr/nportal/buySupprt/psPopupLocalCarModelPrice.do?"
    "year=2026&local_cd=1100&local_nm=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C&car_type=11"
)


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
        "--no-first-run",
        "--window-size=1440,1200",
    ):
        options.add_argument(argument)
    return webdriver.Chrome(options=options)


def render(url: str, expected_text: str, output: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        driver = None
        try:
            driver = new_driver()
            driver.set_page_load_timeout(45)
            try:
                driver.get(url)
            except TimeoutException:
                # The portal keeps background connections alive. The table can
                # still be ready, so continue to the explicit DOM wait below.
                pass
            WebDriverWait(driver, 45, poll_frequency=0.5).until(
                lambda current: expected_text
                in current.execute_script("return document.body ? document.body.innerText : ''")
            )
            rendered = driver.page_source
            if expected_text not in rendered:
                raise RuntimeError(f"렌더링된 표를 찾지 못했습니다: {expected_text}")
            output.write_text(rendered, encoding="utf-8")
            print(f"Rendered {expected_text} -> {output}")
            return
        except (TimeoutException, WebDriverException, RuntimeError) as error:
            last_error = error
            print(f"Official page render retry {attempt}/3: {error}")
            time.sleep(attempt * 3)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass
    raise RuntimeError(f"공식 페이지 렌더링 실패: {expected_text}") from last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payment-output", type=Path, required=True)
    parser.add_argument("--price-output", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, required=True)
    args = parser.parse_args()
    render(PAYMENT_URL, "지자체별 무공해차 구매보조금 지급현황", args.payment_output)
    render(PRICE_URL, "전기자동차 지자체 차종별 보조금 목록", args.price_output)
    render(MODEL_URL, "전기자동차 모델별 보조금 목록", args.model_output)


if __name__ == "__main__":
    main()
