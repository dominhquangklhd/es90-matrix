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

    def test_normalize_date_text(self) -> None:
        self.assertEqual(updater.normalize_date_text("2026-08-10"), "260810")
        self.assertEqual(updater.normalize_date_text("26.08.10"), "260810")

    def test_github_output_contains_no_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.txt"
            with patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}):
                updater.write_github_output(True, 4, 1)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "changed=true\nscanned_count=4\nnew_count=1\n",
            )


if __name__ == "__main__":
    unittest.main()
