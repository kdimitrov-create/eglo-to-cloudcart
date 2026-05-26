"""Download EGLO product HTML pages into ./cache/ for codes listed in PRODUCT_CODES."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from eglo_to_cloudcart import PRODUCT_CODES, SOURCE_DIR


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch(code: str) -> Path:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://www.eglo.com/bg/catalogsearch/result/?q={code}"
    out = SOURCE_DIR / f"search_{code}.html"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as r:
        out.write_bytes(r.read())
    return out


def main() -> None:
    for code in PRODUCT_CODES:
        path = fetch(code)
        print(f"Saved {path}")


if __name__ == "__main__":
    main()
