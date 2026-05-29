"""Parse eglo.com/bg sitemap into category and product URL lists."""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


SITEMAP_URL = "https://www.eglo.com/sitemaps/sitemap_bg.xml"
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

PRODUCT_CODE_RE = re.compile(r"-(\d{5,})\.html$")


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str
    path: str  # path relative to /bg/, without .html


def fetch_sitemap(cache: Path | None = None) -> bytes:
    if cache and cache.exists():
        return cache.read_bytes()
    with urllib.request.urlopen(SITEMAP_URL, timeout=60) as r:
        data = r.read()
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    return data


def parse(xml_bytes: bytes) -> tuple[dict[str, SitemapEntry], dict[str, SitemapEntry]]:
    """Return (products, categories) dicts.

    Products key = product code (string).
    Categories key = url_handle (slug path with `/` separators).
    """
    root = ET.fromstring(xml_bytes)
    products: dict[str, SitemapEntry] = {}
    categories: dict[str, SitemapEntry] = {}

    for u in root.findall(f"{NS}url"):
        loc = u.findtext(f"{NS}loc", "")
        lastmod = u.findtext(f"{NS}lastmod", "")
        if not loc.startswith("https://www.eglo.com/bg/"):
            continue
        path = loc.replace("https://www.eglo.com/bg/", "").rstrip("/")
        if not path or path.endswith(".html") is False:
            continue
        path_no_ext = path[:-5]

        m = PRODUCT_CODE_RE.search(loc)
        if m:
            code = m.group(1)
            products[code] = SitemapEntry(loc, lastmod, path_no_ext)
        else:
            categories[path_no_ext] = SitemapEntry(loc, lastmod, path_no_ext)

    return products, categories


def category_tree(categories: dict[str, SitemapEntry]) -> dict[str, list[str]]:
    """Return parent_path -> [child_path, ...] mapping (single level)."""
    tree: dict[str, list[str]] = {"": []}
    for path in categories:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        tree.setdefault(parent, []).append(path)
    return tree
