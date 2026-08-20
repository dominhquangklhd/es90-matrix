#!/usr/bin/env python3
import unittest

from render_subsidy_pages import selection_breakdown_from_rows


class SelectionBreakdownTests(unittest.TestCase):
    def test_builds_valid_breakdown_from_dom_snapshot(self) -> None:
        rows = [
            {"category": "전체", "selected": "15,190대"},
            {"category": "일반", "selected": "9,079"},
            {"category": "우선순위", "selected": "4,426"},
            {"category": "택시", "selected": "1,199"},
            {"category": "법인·기관", "selected": "486"},
        ]

        self.assertEqual(
            {
                "general": 9079,
                "priority": 4426,
                "taxi": 1199,
                "corporate": 486,
                "total": 15190,
            },
            selection_breakdown_from_rows("서울특별시", rows),
        )

    def test_rejects_inconsistent_breakdown(self) -> None:
        rows = [
            {"category": "전체", "selected": "100"},
            {"category": "일반", "selected": "90"},
            {"category": "우선순위", "selected": "9"},
            {"category": "택시", "selected": "0"},
            {"category": "법인·기관", "selected": "0"},
        ]

        with self.assertRaisesRegex(RuntimeError, "선정 세부 합계 불일치"):
            selection_breakdown_from_rows("테스트광역시", rows)


if __name__ == "__main__":
    unittest.main()
