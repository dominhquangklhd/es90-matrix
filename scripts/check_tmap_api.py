#!/usr/bin/env python3
import re
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHARGER_PATH = ROOT / "charger.html"


def main() -> int:
    source = CHARGER_PATH.read_text(encoding="utf-8")
    match = re.search(
        r'<script\s+src="(?P<url>https://apis\.openapi\.sk\.com/tmap/jsv2\?[^"]+)"',
        source,
    )
    if not match:
        print("TMAP SDK URL을 charger.html에서 찾지 못했습니다.", file=sys.stderr)
        return 1

    request = urllib.request.Request(
        match.group("url"),
        headers={"User-Agent": "ES90-TMAP-Daily-Health-Check/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", errors="ignore")
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            if "Tmapv2" not in body:
                raise RuntimeError("TMAP SDK 응답에서 Tmapv2를 찾지 못했습니다.")
    except Exception as error:
        print(f"TMAP SDK 일일 점검 실패: {error}", file=sys.stderr)
        return 1

    print("TMAP SDK 일일 점검 성공")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
