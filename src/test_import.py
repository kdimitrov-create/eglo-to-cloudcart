"""End-to-end MVP test: import 2 EGLO products into bronson.cloudcart.net.

Reads PAT from env var CLOUDCART_PAT.

Steps:
1. Ensure a root "EGLO" category exists (create if missing).
2. For each test SKU:
   - Parse cached HTML (uses eglo_to_cloudcart.parse_product)
   - find_product_by_sku — skip if already imported
   - createProduct
   - attach_categories([EGLO root])
   - uploadProductImage for each gallery image (first = primary)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cloudcart_client import CloudCartClient, CloudCartError
from eglo_to_cloudcart import parse_product, SOURCE_DIR


TEST_SKUS = ["901565", "110181"]
ROOT_CATEGORY_NAME = "EGLO"
ROOT_CATEGORY_HANDLE = "eglo"


def ensure_root_category(client: CloudCartClient) -> str:
    existing = client.find_category_by_handle(ROOT_CATEGORY_HANDLE)
    if existing:
        print(f"  [skip] Root category 'EGLO' exists (id={existing['id']})")
        return existing["id"]
    cat = client.create_category(ROOT_CATEGORY_NAME, ROOT_CATEGORY_HANDLE)
    print(f"  [new]  Created root category 'EGLO' (id={cat['id']})")
    return cat["id"]


def build_create_input(prod: dict, root_category_id: str) -> dict:
    tabs = [
        {"name": label, "description": html, "sort_order": i}
        for i, (label, html) in enumerate(prod["tabs"].items())
    ]
    price_float = float(prod["price"]) if prod["price"] else 0.0
    inp: dict = {
        "name": prod["title"],
        "sku": prod["code"],
        "barcode": prod["barcode"],
        "price": price_float,
        "short_description": prod["short_description"][:1000],
        "description": prod["description"],
        "seo_title": prod["meta_title"][:160],
        "seo_description": prod["meta_description"][:300],
        "active": "yes",
        "category_id": root_category_id,
        "minimum": 1,
    }
    if tabs:
        inp["tabs"] = tabs
    return inp


def import_product(client: CloudCartClient, sku: str, root_category_id: str) -> None:
    html_path = SOURCE_DIR / f"search_{sku}.html"
    if not html_path.exists():
        print(f"[{sku}] no cached HTML, skipping")
        return

    existing = client.find_product_by_sku(sku)
    if existing:
        print(f"[{sku}] already exists (id={existing['id']}), skipping")
        return

    prod = parse_product(sku, html_path)
    print(f"[{sku}] parsed: '{prod['title']}' price={prod['price']} images={len(prod['images'])}")

    inp = build_create_input(prod, root_category_id)
    created = client.create_product(inp)
    pid = created["id"]
    print(f"[{sku}] created product id={pid}")

    for i, url in enumerate(prod["images"]):
        try:
            img = client.upload_product_image_from_url(
                pid, url, name=prod["title"], sort_order=i, set_primary=(i == 0),
            )
            print(f"[{sku}]   uploaded image {i+1}/{len(prod['images'])} (id={img['id']})")
        except Exception as e:
            print(f"[{sku}]   FAILED image {i+1}: {e}")


def main() -> int:
    client = CloudCartClient()
    print(f"Connected to {client.store}")

    root_id = ensure_root_category(client)
    print()
    for sku in TEST_SKUS:
        try:
            import_product(client, sku, root_id)
        except CloudCartError as e:
            print(f"[{sku}] GraphQL error: {e}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
