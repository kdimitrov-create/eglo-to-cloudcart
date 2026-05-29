"""Main sync pipeline: scrape EGLO and push to bronson.cloudcart.net via GraphQL.

Workflow (designed to be run on a schedule, e.g. every 8 hours):

  1. Refresh sitemap_bg.xml -> get current products + lastmod
  2. Load state/products.json from repo (last seen lastmod, CloudCart id per SKU)
  3. Diff:
       - new SKUs                  -> scrape + createProduct + uploadImages + attach
       - changed lastmod           -> scrape + updateProduct (+ re-upload images if changed)
       - SKUs no longer in sitemap -> productsBulkSetActive(no)
       - unchanged                 -> skip
  4. Persist updated state/products.json (committed by the GH Action)

Reads PAT from CLOUDCART_PAT env var. Reads category mapping from
cache/category_map.json + cache/cloudcart_category_ids.json (run
category_mapper.py and mirror_categories.py first; cron job runs them too).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cloudcart_client import CloudCartClient, CloudCartError
from eglo_to_cloudcart import parse_product
from sitemap_parser import fetch_sitemap, parse as parse_sitemap


REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "cache"
STATE_DIR = REPO_ROOT / "state"
STATE_PATH = STATE_DIR / "products.json"
CATEGORY_MAP_PATH = CACHE / "category_map.json"
CC_IDS_PATH = CACHE / "cloudcart_category_ids.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch_product_html(code: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"search_{code}.html"
    url = f"https://www.eglo.com/bg/catalogsearch/result/?q={code}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        out.write_bytes(r.read())
    return out


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_category_ids() -> tuple[dict, dict]:
    cat_map = json.loads(CATEGORY_MAP_PATH.read_text(encoding="utf-8")) if CATEGORY_MAP_PATH.exists() else {"products": {}}
    cc_ids = json.loads(CC_IDS_PATH.read_text(encoding="utf-8")) if CC_IDS_PATH.exists() else {}
    return cat_map.get("products", {}), cc_ids


def resolve_category_ids(sku: str, product_to_cats: dict, cc_ids: dict) -> list[str]:
    handles = product_to_cats.get(sku, [])
    return [cc_ids[h] for h in handles if h in cc_ids]


def build_input(prod: dict, primary_category_id: str | None) -> dict:
    tabs = [
        {"name": label, "description": html, "sort_order": i}
        for i, (label, html) in enumerate(prod["tabs"].items())
    ]
    price = float(prod["price"]) if prod["price"] else 0.0
    inp: dict = {
        "name": prod["title"],
        "sku": prod["code"],
        "barcode": prod["barcode"],
        "price": price,
        "short_description": prod["short_description"][:1000],
        "description": prod["description"],
        "seo_title": prod["meta_title"][:160],
        "seo_description": prod["meta_description"][:300],
        "active": "yes",
        "minimum": 1,
    }
    if primary_category_id:
        inp["category_id"] = primary_category_id
    if tabs:
        inp["tabs"] = tabs
    return inp


def sync_one(
    client: CloudCartClient, code: str, lastmod: str, state: dict,
    product_to_cats: dict, cc_ids: dict, upload_images: bool,
) -> str:
    prior = state.get(code, {})

    if prior.get("lastmod") == lastmod and prior.get("cloudcart_id"):
        return "unchanged"

    html_path = fetch_product_html(code, CACHE)
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    if "Не бяха намерени" in html:
        return "missing"

    prod = parse_product(code, html_path)
    cat_ids = resolve_category_ids(code, product_to_cats, cc_ids)
    primary_cat = cat_ids[0] if cat_ids else None
    inp = build_input(prod, primary_cat)

    cc_id = prior.get("cloudcart_id")
    if cc_id:
        client.update_product(cc_id, inp)
        action = "updated"
    else:
        existing = client.find_product_by_sku(code)
        if existing:
            cc_id = existing["id"]
            client.update_product(cc_id, inp)
            action = "adopted"
        else:
            created = client.create_product(inp)
            cc_id = created["id"]
            action = "created"

    if len(cat_ids) > 1:
        try:
            client.attach_categories(cc_id, cat_ids[1:])
        except CloudCartError as e:
            print(f"  [{code}] attach_categories failed: {e}")

    if upload_images and action in ("created", "adopted"):
        for i, url in enumerate(prod["images"]):
            try:
                client.upload_product_image_from_url(
                    cc_id, url, name=prod["title"], sort_order=i, set_primary=(i == 0),
                )
            except Exception as e:
                print(f"  [{code}] image {i+1} failed: {e}")

    state[code] = {
        "cloudcart_id": cc_id,
        "lastmod": lastmod,
        "price": prod["price"],
        "title": prod["title"],
    }
    return action


def deactivate_removed(client: CloudCartClient, removed_codes: list[str], state: dict) -> int:
    ids = [state[c]["cloudcart_id"] for c in removed_codes if c in state and state[c].get("cloudcart_id")]
    if not ids:
        return 0
    try:
        client.deactivate_products(ids)
    except CloudCartError as e:
        print(f"deactivate failed: {e}")
        return 0
    for c in removed_codes:
        if c in state:
            state[c]["active"] = False
    return len(ids)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="process at most N changed/new products (0 = all)")
    p.add_argument("--no-images", action="store_true", help="skip image uploads (faster iteration)")
    p.add_argument("--only", nargs="*", help="restrict to these SKUs")
    args = p.parse_args()

    client = CloudCartClient()
    print(f"Sync starting against {client.store}")

    sitemap_bytes = fetch_sitemap(CACHE / "sitemap_bg.xml")
    sitemap_products, _ = parse_sitemap(sitemap_bytes)
    print(f"Sitemap: {len(sitemap_products)} products")

    state = load_state()
    print(f"State: {len(state)} previously synced SKUs")

    product_to_cats, cc_ids = load_category_ids()
    print(f"Category mapping: {len(product_to_cats)} products -> {len(cc_ids)} CloudCart categories")

    work: list[tuple[str, str]] = []
    for code, entry in sitemap_products.items():
        if args.only and code not in args.only:
            continue
        prior = state.get(code, {})
        if prior.get("lastmod") != entry.lastmod or not prior.get("cloudcart_id"):
            work.append((code, entry.lastmod))
    if args.limit:
        work = work[: args.limit]

    removed = [c for c in state if c not in sitemap_products]
    print(f"Work: {len(work)} new/changed, {len(removed)} removed")

    counts: dict[str, int] = {}
    for i, (code, lastmod) in enumerate(work, 1):
        try:
            action = sync_one(client, code, lastmod, state,
                              product_to_cats, cc_ids, upload_images=not args.no_images)
        except CloudCartError as e:
            print(f"  [{code}] {e}")
            action = "error"
        except Exception as e:
            print(f"  [{code}] unexpected: {e}")
            action = "error"
        counts[action] = counts.get(action, 0) + 1
        if i % 20 == 0 or i == len(work):
            print(f"  [{i}/{len(work)}] last={code} action={action}")
        if i % 50 == 0:
            save_state(state)

    if removed:
        deactivated = deactivate_removed(client, removed, state)
        counts["deactivated"] = deactivated

    save_state(state)
    print(f"Done: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
