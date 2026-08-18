import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_cdsid_authorizations as updater


class CdsidAuthorizationTests(unittest.TestCase):
    def test_hash_matches_browser_sha256_normalization(self) -> None:
        self.assertEqual(
            updater.hash_cdsid("  a-b12  "),
            updater.hash_cdsid("A-B12"),
        )
        self.assertEqual(len(updater.hash_cdsid("A-B12")), 64)

    def test_append_adds_only_missing_hashes(self) -> None:
        first = updater.hash_cdsid("A-ONE")
        second = updater.hash_cdsid("B-TWO")
        source = (
            "before\nconst AUTHORIZED_CDSID_HASHES = new Set([\n"
            f"  '{first}'\n"
            "]);\nafter\n"
        )
        updated = updater.append_authorized_hashes(source, {first, second})
        self.assertEqual(updated.count(first), 1)
        self.assertEqual(updated.count(second), 1)
        self.assertEqual(updater.read_authorized_hashes(updated), [first, second])

    def test_update_revokes_departed_and_departure_wins(self) -> None:
        active = updater.hash_cdsid("ACTIVE-ONE")
        departed = updater.hash_cdsid("DEPARTED-TWO")
        source = (
            "before\nconst AUTHORIZED_CDSID_HASHES = new Set([\n"
            f"  '{active}',\n"
            f"  '{departed}'\n"
            "]);\nafter\n"
        )
        updated = updater.update_authorized_hashes(
            source,
            {departed},
            {departed},
        )
        self.assertEqual(updater.read_authorized_hashes(updated), [active])

    def test_rehire_on_or_after_departure_restores_access(self) -> None:
        hired, departed = updater.resolve_access_events(
            {
                "RETURNED-LATER": "260815",
                "RETURNED-SAME-DAY": "260814",
                "LEFT-LATER": "260812",
            },
            {
                "RETURNED-LATER": "260812",
                "RETURNED-SAME-DAY": "260814",
                "LEFT-LATER": "260817",
            },
        )
        self.assertEqual(hired, {"RETURNED-LATER", "RETURNED-SAME-DAY"})
        self.assertEqual(departed, {"LEFT-LATER"})

    def test_target_roles_and_baseline_date(self) -> None:
        self.assertEqual(updater.START_DATE, "260811")
        self.assertEqual(
            updater.TARGET_ROLES,
            (
                "영업직원",
                "영업팀장",
                "스페셜리스트",
                "세일즈 본부장",
                "세일즈 지점장",
            ),
        )

    def test_normalize_date_text(self) -> None:
        self.assertEqual(updater.normalize_date_text("2026-08-10"), "260810")
        self.assertEqual(updater.normalize_date_text("26.08.10"), "260810")

    def test_github_output_contains_no_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.txt"
            with patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}):
                updater.write_github_output(
                    True,
                    4,
                    1,
                    checked_at="2026-08-18T09:07:00+09:00",
                )
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "changed=true\n"
                "scanned_count=4\n"
                "new_count=1\n"
                "departed_count=0\n"
                "revoked_count=0\n"
                "checked_at=2026-08-18T09:07:00+09:00\n",
            )


if __name__ == "__main__":
    unittest.main()
