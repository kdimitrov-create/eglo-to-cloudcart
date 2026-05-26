"""
EGLO -> CloudCart XML scraper / converter.

Reads pre-downloaded EGLO product HTML pages from c:/temp/eglo/search_<code>.html
and emits a CloudCart-compatible <products> XML feed.

Tab IDs on eglo.com/bg product pages:
    description           -> "Описание на продукта"
    dimensions            -> "Размери"
    technical-information -> "Техническа информация"
    further-information   -> "Допълнителна информация"  (contains EAN)
    download-information  -> "Информация за изтегляне"  (PDFs)

"Продуктови детайли" key/value list lives in div.product-detailed table.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

from bs4 import BeautifulSoup


PRODUCT_CODES = ["901565", "12263", "110024", "110181", "901124", "43977"]

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "cache"
OUTPUT_PATH = REPO_ROOT / "output" / "eglo_cloudcart_feed.xml"

MANUFACTURER = "EGLO"

TAB_LABELS = {
    "description":           "Описание на продукта",
    "dimensions":            "Размери",
    "technical-information": "Техническа информация",
    "further-information":   "Допълнителна информация",
    "download-information":  "Информация за изтегляне",
}


def parse_gallery(html: str) -> list[str]:
    """Return full-size image URLs in display order from Magento gallery JSON."""
    m = re.search(
        r'"mage/gallery/gallery"\s*:\s*\{.*?"data"\s*:\s*(\[.+?\])\s*,\s*"options"',
        html, re.DOTALL,
    )
    if not m:
        return []
    try:
        items = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    items.sort(key=lambda x: (not x.get("isMain", False), int(x.get("position", 0))))
    return [it["full"] for it in items if it.get("type") == "image" and it.get("full")]


def parse_ld_json(html: str) -> dict:
    """Find the JSON-LD block describing the Product (skipping Organization etc.)."""
    for m in re.finditer(r'<script type="application/ld\+json">(.+?)</script>', html, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if data.get("@type") == "Product":
            return data
    return {}


def slug_from_html_path(html_path: Path, code: str) -> str:
    """Recover the product URL slug from the saved HTML's canonical/og:url tag."""
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'rel="canonical"\s+href="https://www\.eglo\.com/bg/([^"]+)\.html"', html)
    if m:
        return m.group(1)
    m = re.search(r'og:url"\s+content="https://www\.eglo\.com/bg/([^"]+)\.html"', html)
    return m.group(1) if m else ""


def category_from_slug(slug: str, code: str) -> str:
    """Strip the trailing `-<code>` and turn the slug stem into a human category guess."""
    stem = re.sub(rf"-{re.escape(code)}$", "", slug)
    if not stem or stem == slug:
        return ""
    return stem.replace("-", " ").strip().capitalize()


def kv_from_table(table) -> list[tuple[str, str]]:
    """Return list of (label, value) tuples from an attribute-row table."""
    pairs: list[tuple[str, str]] = []
    if not table:
        return pairs
    for row in table.select("tr.attribute-row"):
        label_el = row.select_one("td.attribute-label")
        value_el = row.select_one("td.attribute-value")
        if not (label_el and value_el):
            continue
        label = label_el.get_text(" ", strip=True).rstrip(":").strip()
        value = value_el.get_text(" ", strip=True)
        if label:
            pairs.append((label, value))
    return pairs


def files_from_table(table) -> list[dict]:
    """Extract download links with proper labels from attribute-row table."""
    files = []
    if not table:
        return files
    for row in table.select("tr.attribute-row"):
        label_el = row.select_one("td.attribute-label")
        a = row.select_one("td.attribute-value a")
        if not (label_el and a):
            continue
        href = a.get("href", "").strip()
        label = label_el.get_text(" ", strip=True).rstrip(":").strip()
        if href and label and href.startswith("http"):
            files.append({"name": label, "url": href})
    return files


def parse_product(code: str, html_path: Path) -> dict:
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    ld = parse_ld_json(html)

    raw_name = (ld.get("name") or "").strip()
    raw_name = re.sub(r"\s*-\s*$", "", raw_name).strip()
    if not raw_name:
        h1 = soup.select_one("h1 .base") or soup.select_one("h1")
        raw_name = (h1.get_text(strip=True) if h1 else "")
        raw_name = re.sub(r"\s*-\s*$", "", raw_name).strip()
    title = f"{raw_name} - {code}" if raw_name else f"EGLO {code}"

    price = ""
    if isinstance(ld.get("offers"), dict):
        price = str(ld["offers"].get("price", "")).replace(",", ".")
    if not price:
        pe = soup.select_one("[data-price-amount]")
        if pe:
            price = pe.get("data-price-amount", "").replace(",", ".")

    desc_el = soup.select_one("div.data.item.content#description")
    description = ""
    if desc_el:
        inner = desc_el.find("div")
        description = (inner.decode_contents() if inner else desc_el.decode_contents()).strip()

    short_description = ld.get("description", "").strip()

    slug = slug_from_html_path(html_path, code)
    category = category_from_slug(slug, code) or "EGLO"
    sub_category = ""

    images = parse_gallery(html)

    product_details = kv_from_table(
        soup.select_one("div.product-detailed .product-data-items table")
    )

    tabs_kv: dict[str, list[tuple[str, str]]] = {}
    for tab_id, label in TAB_LABELS.items():
        if tab_id in ("description", "download-information"):
            continue
        panel = soup.select_one(f"div.data.item.content#{tab_id}")
        if panel:
            tabs_kv[label] = kv_from_table(panel.select_one("table"))

    download_panel = soup.select_one("div.data.item.content#download-information")
    files = files_from_table(download_panel.select_one("table")) if download_panel else []

    ean = ""
    for k, v in tabs_kv.get(TAB_LABELS["further-information"], []):
        if "ean" in k.lower():
            ean = re.sub(r"\D", "", v)
            break

    all_props: list[tuple[str, str]] = []
    all_props.extend(product_details)
    for label, pairs in tabs_kv.items():
        for k, v in pairs:
            all_props.append((k, v))

    meta_title = ""
    meta_desc = ""
    mt = soup.find("meta", attrs={"name": "title"})
    if mt:
        meta_title = mt.get("content", "")
    md = soup.find("meta", attrs={"name": "description"})
    if md:
        meta_desc = md.get("content", "")

    tabs_out: dict[str, str] = {}
    desc_panel = soup.select_one("div.data.item.content#description")
    if desc_panel:
        tabs_out[TAB_LABELS["description"]] = desc_panel.decode_contents().strip()
    for tab_id, label in TAB_LABELS.items():
        if tab_id == "description":
            continue
        panel = soup.select_one(f"div.data.item.content#{tab_id}")
        if panel:
            tabs_out[label] = panel.decode_contents().strip()

    product_url = ""
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        product_url = canon["href"]

    return {
        "code": code,
        "title": title,
        "short_description": short_description,
        "description": description,
        "category": category,
        "sub_category": sub_category,
        "price": price,
        "barcode": ean or code,
        "url": product_url,
        "meta_title": meta_title,
        "meta_description": meta_desc,
        "properties": all_props,
        "product_details": product_details,
        "images": images,
        "files": files,
        "tabs": tabs_out,
    }


def build_xml(products: list[dict]) -> bytes:
    """Emit CloudCart import XML matching the production feed format
    (kaisai-bulgaria.com synch-xml export). Tag order is significant for the
    CloudCart importer's visual diffs, so we keep it identical to the reference.
    """
    root = Element("products")
    for p in products:
        prod = SubElement(root, "product")
        SubElement(prod, "id").text = ""
        SubElement(prod, "product_code").text = p["code"]
        SubElement(prod, "barcode").text = p["barcode"]
        SubElement(prod, "title").text = p["title"]
        SubElement(prod, "short_description").text = p["short_description"]
        SubElement(prod, "description").text = p["description"]
        SubElement(prod, "minimum").text = "1"
        SubElement(prod, "manufacturer").text = MANUFACTURER
        SubElement(prod, "weight").text = ""
        SubElement(prod, "sku").text = p["code"]
        SubElement(prod, "meta_title").text = p["meta_title"]
        SubElement(prod, "meta_description").text = p["meta_description"]
        SubElement(prod, "url").text = p["url"]
        SubElement(prod, "category").text = p["category"]
        SubElement(prod, "sub_category").text = p["sub_category"]

        if p["properties"]:
            cp_root = SubElement(prod, "category_properties")
            for name, value in p["properties"]:
                cp = SubElement(cp_root, "category_property", {"name": name})
                vs = SubElement(cp, "values")
                v = SubElement(vs, "value")
                SubElement(v, "name").text = value

        if p["tabs"]:
            tabs_el = SubElement(prod, "tabs")
            for label, html in p["tabs"].items():
                tab_el = SubElement(tabs_el, "tab")
                SubElement(tab_el, "name").text = label
                SubElement(tab_el, "description").text = html

        SubElement(prod, "price").text = p["price"]
        SubElement(prod, "original_price").text = p["price"]
        SubElement(prod, "quantity").text = ""

        if p["images"]:
            imgs = SubElement(prod, "images")
            for url in p["images"]:
                SubElement(imgs, "image").text = url

    raw = tostring(root, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
    return pretty


def main() -> int:
    products = []
    missing = []
    for code in PRODUCT_CODES:
        path = SOURCE_DIR / f"search_{code}.html"
        if not path.exists():
            missing.append(code)
            continue
        html = path.read_text(encoding="utf-8", errors="ignore")
        if "Резултати от търсенето" in html and "Не бяха намерени" in html:
            missing.append(code)
            continue
        try:
            products.append(parse_product(code, path))
        except Exception as e:
            print(f"[ERROR] {code}: {e}", file=sys.stderr)

    xml_bytes = build_xml(products)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(xml_bytes)
    print(f"Wrote {len(products)} products to {OUTPUT_PATH}")
    if missing:
        print(f"Missing / not found: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
