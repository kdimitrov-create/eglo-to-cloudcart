"""Crawl EGLO category pages to build product_code -> [category_handle] mapping.

Strategy: only categories rooted in one of the known top-level slugs are real
product categories. Sale/blog/inspiration are excluded — they are non-product
views that would clutter the catalog tree on the CloudCart side.

Output (written to cache/category_map.json):
{
  "categories": {
    "eksteriorno-osvetlenie":            {"name": "Екстериорно осветление", "parent": null},
    "eksteriorno-osvetlenie/aleyno-...": {"name": "Алейно осветление",       "parent": "eksteriorno-osvetlenie"},
    ...
  },
  "products": {
    "901565": ["eksteriorno-osvetlenie/...", ...],
    ...
  }
}
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sitemap_parser import fetch_sitemap, parse


REAL_TOP_SLUGS = {
    "eksteriorno-osvetlenie",
    "interiorno-osvetlenie",
    "krushki",
    "smart-osvetlenie",
    "svetnik",
    "dekoraciya-i-aksesoari-za-doma",
    "ventilatori",
    "koledno-osvetlenie",
    "eglo-expert",
}

BASE = "https://www.eglo.com/bg/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

PRODUCT_LINK_RE = re.compile(r"/bg/([a-z0-9-]+-\d{4,})\.html")
TITLE_RE = re.compile(
    r'<span class="base"\s+data-ui-id="page-title-wrapper"[^>]*>([^<]+)</span>'
)
PRODUCT_CODE_RE = re.compile(r"-(\d{4,})$")
PRODUCT_LIST_RE = re.compile(
    r'<ol class="products list items product-items"[^>]*>(.*?)</ol>', re.DOTALL,
)
TOTAL_COUNT_RE = re.compile(
    r'<p class="toolbar-amount desktop"[^>]*>.*?'
    r'<span class="toolbar-number">\d+</span>-<span class="toolbar-number">\d+</span>'
    r'\s*от\s*<span class="toolbar-number">(\d+)</span>',
    re.DOTALL,
)
PAGE_SIZE = 24


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="ignore")


def fetch_category_page(handle: str, page: int = 1) -> tuple[str, set[str], int]:
    """Return (display_name, set_of_product_codes_on_page, total_count_in_category)."""
    url = f"{BASE}{handle}.html" + (f"?p={page}" if page > 1 else "")
    html = http_get(url)

    name = ""
    m = TITLE_RE.search(html)
    if m:
        name = m.group(1).strip()

    codes: set[str] = set()
    list_match = PRODUCT_LIST_RE.search(html)
    scope = list_match.group(1) if list_match else ""
    for m in PRODUCT_LINK_RE.finditer(scope):
        slug = m.group(1)
        cm = PRODUCT_CODE_RE.search(slug)
        if cm:
            codes.add(cm.group(1))

    tm = TOTAL_COUNT_RE.search(html)
    total = int(tm.group(1)) if tm else len(codes)
    return name, codes, total


def crawl_category(handle: str) -> tuple[str, set[str]]:
    """Return (display_name, all_product_codes_across_all_pages)."""
    name, codes, total = fetch_category_page(handle, 1)
    max_page = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    for p in range(2, max_page + 1):
        _, more, _ = fetch_category_page(handle, p)
        codes.update(more)
    return name, codes


def select_real_categories(all_cats: dict) -> list[str]:
    real: list[str] = []
    for path in all_cats:
        top = path.split("/", 1)[0]
        if top in REAL_TOP_SLUGS:
            real.append(path)
    for top in REAL_TOP_SLUGS:
        if top not in real and top in all_cats:
            real.append(top)
    return sorted(set(real))


def build_map(cache_dir: Path, max_workers: int = 8) -> dict:
    sitemap = fetch_sitemap(cache_dir / "sitemap_bg.xml")
    _products, all_cats = parse(sitemap)
    real_handles = select_real_categories(all_cats)
    print(f"Real category URLs to crawl: {len(real_handles)}")

    cats_meta: dict[str, dict] = {}
    product_to_cats: dict[str, list[str]] = {}

    start = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(crawl_category, h): h for h in real_handles}
        for i, fut in enumerate(as_completed(futures), 1):
            handle = futures[fut]
            try:
                name, codes = fut.result()
            except Exception as e:
                print(f"  [{i}/{len(real_handles)}] FAILED {handle}: {e}")
                continue
            parent = handle.rsplit("/", 1)[0] if "/" in handle else None
            cats_meta[handle] = {"name": name or handle, "parent": parent}
            for code in codes:
                product_to_cats.setdefault(code, []).append(handle)
            if i % 10 == 0 or i == len(real_handles):
                print(f"  [{i}/{len(real_handles)}] {handle} -> {len(codes)} products")

    elapsed = time.time() - start
    print(f"Crawled {len(cats_meta)} categories in {elapsed:.1f}s")
    print(f"Products mapped: {len(product_to_cats)}")

    return {"categories": cats_meta, "products": product_to_cats}


def main() -> int:
    cache = Path(__file__).resolve().parent.parent / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    data = build_map(cache)
    out = cache / "category_map.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
