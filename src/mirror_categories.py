"""Mirror EGLO category tree into CloudCart.

Reads cache/category_map.json (produced by category_mapper.py) and ensures every
EGLO category exists in CloudCart with the correct parent_id. Idempotent —
existing categories are skipped, missing ones are created.

Writes cache/cloudcart_category_ids.json mapping
EGLO url_handle -> CloudCart category id, used downstream by the importer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cloudcart_client import CloudCartClient, CloudCartError


CACHE = Path(__file__).resolve().parent.parent / "cache"
CATEGORY_MAP_PATH = CACHE / "category_map.json"
CC_IDS_PATH = CACHE / "cloudcart_category_ids.json"

# url_handles in CloudCart map to a flat key — we slugify EGLO multi-level
# paths the same way the EGLO site does ("a/b" -> "a-b").
def cc_handle(egl_path: str) -> str:
    return egl_path.replace("/", "-")


def topological_order(categories: dict) -> list[str]:
    """Return EGLO paths sorted so each parent is created before its children."""
    return sorted(categories.keys(), key=lambda p: (p.count("/"), p))


def mirror(client: CloudCartClient) -> dict[str, str]:
    cat_map = json.loads(CATEGORY_MAP_PATH.read_text(encoding="utf-8"))
    cats = cat_map["categories"]
    cc_ids: dict[str, str] = {}
    if CC_IDS_PATH.exists():
        cc_ids = json.loads(CC_IDS_PATH.read_text(encoding="utf-8"))

    order = topological_order(cats)
    print(f"Mirroring {len(order)} categories")

    created = skipped = errors = 0
    for path in order:
        if path in cc_ids:
            skipped += 1
            continue
        meta = cats[path]
        name = meta["name"]
        handle = cc_handle(path)
        parent_egl = meta["parent"]
        parent_id = cc_ids.get(parent_egl) if parent_egl else None

        existing = client.find_category_by_handle(handle)
        if existing:
            cc_ids[path] = existing["id"]
            skipped += 1
            continue
        try:
            cat = client.create_category(name=name, url_handle=handle, parent_id=parent_id)
            cc_ids[path] = cat["id"]
            created += 1
            print(f"  [new] {path} -> id={cat['id']} (parent={parent_id})")
        except CloudCartError as e:
            errors += 1
            print(f"  [ERR] {path}: {e}")

    CC_IDS_PATH.write_text(json.dumps(cc_ids, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done — created: {created}, skipped: {skipped}, errors: {errors}")
    print(f"Wrote {CC_IDS_PATH}")
    return cc_ids


def main() -> int:
    client = CloudCartClient()
    mirror(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
