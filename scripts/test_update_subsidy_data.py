#!/usr/bin/env python3
"""Regression tests for legacy/new EV portal subsidy layouts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from update_subsidy_data import build_snapshot


def payment_html(region_count: int) -> str:
    rows = []
    for index in range(region_count):
        rows.append(
            "<tr>"
            f"<td>서울</td><td>지역{index}</td><td>전기승용</td>"
            "<td>본공고</td><td>출고등록순</td>"
            "<td>2026.12.04 18:00</td>"
            "<td>100</td>"
            "<td>80</td>"
            "<td>70</td>"
            "<td>60</td>"
            "<td>30</td>"
            "<td>40</td>"
            "</tr>"
        )
    return (
        "<html><head><meta charset='utf-8'></head><body>"
        "<table><caption>지자체별 무공해차 구매보조금 지급현황</caption>"
        "<tr><th>header</th></tr>" + "".join(rows) + "</table>"
        "</body></html>"
    )


def new_price_html() -> str:
    return """<html><head><meta charset="utf-8"></head><body>
    <div class="accordion-item" data-local-cd="1100">
      <b class="location__district">지역0</b>
      <span class="location__city">서울</span>
      <div role="row" row-index="0">
        <div role="gridcell" col-id="carNm">전기승용 일반승용</div>
        <div role="gridcell" col-id="maker">볼보</div>
        <div role="gridcell" col-id="model">Volvo ES90 Test</div>
        <div role="gridcell" col-id="gov">648</div>
        <div role="gridcell" col-id="local">194</div>
        <div role="gridcell" col-id="total">842</div>
      </div>
    </div>
    </body></html>
    """


class SubsidySnapshotTests(unittest.TestCase):
    def test_new_price_layout_blends_live_and_fallback_prices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            payment = temp / "payment.html"
            price = temp / "price.html"
            payment.write_text(payment_html(150), encoding="utf-8")
            price.write_text(new_price_html(), encoding="utf-8")
            fallback = {
                "modelStatus": {
                    "name": "Volvo ES90",
                    "officiallyListed": False,
                    "officialModels": [],
                    "message": "previous",
                },
                "regions": [
                    {
                        "sido": "서울특별시",
                        "sigungu": f"지역{index}",
                        "combinedMaxManwon": 842,
                    }
                    for index in range(150)
                ],
            }

            snapshot = build_snapshot(payment, price, fallback_snapshot=fallback)

            self.assertEqual(150, len(snapshot["regions"]))
            self.assertEqual(40, snapshot["regions"][0]["remaining"])
            self.assertEqual(70, snapshot["regions"][0]["selected"])
            self.assertEqual(30, snapshot["regions"][0]["selectionRemaining"])
            self.assertEqual(
                "2026.12.04 18:00",
                snapshot["regions"][0]["applicationDeadline"],
            )
            self.assertEqual(3, snapshot["schemaVersion"])
            self.assertEqual("전기승용 전체", snapshot["allocationBasis"])
            self.assertEqual(842, snapshot["regions"][149]["combinedMaxManwon"])
            self.assertEqual(
                "live-selected-regions-with-last-known-good-fallback",
                snapshot["collection"]["prices"],
            )
            self.assertTrue(snapshot["modelStatus"]["officiallyListed"])

    def test_incomplete_payment_data_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            payment = temp / "payment.html"
            price = temp / "price.html"
            payment.write_text(payment_html(2), encoding="utf-8")
            price.write_text(new_price_html(), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "전국 데이터가 불완전"):
                build_snapshot(payment, price)


if __name__ == "__main__":
    unittest.main()
