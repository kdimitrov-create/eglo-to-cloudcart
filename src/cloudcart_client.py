"""Minimal CloudCart GraphQL client for the EGLO importer.

Reads the API token from the CLOUDCART_PAT environment variable.

Note on `assignPropertyToProduct`: it is documented as destructive
(replace-all), so when updating properties on an existing product we always
send the complete set in one call.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


DEFAULT_STORE = "bronson.cloudcart.net"


class CloudCartError(RuntimeError):
    pass


@dataclass
class CloudCartClient:
    store: str = DEFAULT_STORE
    token: str | None = None
    timeout: int = 60

    def __post_init__(self) -> None:
        self.token = self.token or os.getenv("CLOUDCART_PAT")
        if not self.token:
            raise CloudCartError("CLOUDCART_PAT env var is required")
        self.endpoint = f"https://{self.store}/api/gql"

    def gql(self, query: str, variables: dict | None = None) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                body = e.read().decode("utf-8", errors="ignore")
                raise CloudCartError(f"HTTP {e.code}: {body}") from None
        if "errors" in payload:
            raise CloudCartError(json.dumps(payload["errors"], ensure_ascii=False))
        return payload["data"]

    # ---------- categories ----------

    def list_categories(self) -> list[dict]:
        q = "{ categoryTree(depth:5){ id name urlHandle parentId } }"
        return self.gql(q)["categoryTree"] or []

    def find_category_by_handle(self, url_handle: str) -> dict | None:
        q = "query($h:String!){ categoryByHandle(url_handle:$h){ id name urlHandle parentId } }"
        try:
            data = self.gql(q, {"h": url_handle})
            return data.get("categoryByHandle")
        except CloudCartError:
            return None

    def create_category(self, name: str, url_handle: str, parent_id: str | None = None) -> dict:
        q = """
        mutation($input: CreateCategoryInput!){
          createCategory(input:$input){ id name urlHandle parentId }
        }"""
        inp: dict = {"name": name, "url_handle": url_handle, "active": "yes"}
        if parent_id:
            inp["parent_id"] = parent_id
        return self.gql(q, {"input": inp})["createCategory"]

    # ---------- products ----------

    def find_product_by_sku(self, sku: str) -> dict | None:
        q = """
        query($q:String!){
          productSearch(query:$q){
            edges{ node{ id name inventory{ sku } } }
          }
        }"""
        try:
            data = self.gql(q, {"q": sku})
            edges = (data.get("productSearch") or {}).get("edges") or []
            for e in edges:
                inv = e["node"].get("inventory") or {}
                if inv.get("sku") == sku:
                    return e["node"]
            return None
        except CloudCartError:
            return None

    def create_product(self, input_data: dict) -> dict:
        q = """
        mutation($input: CreateProductInput!){
          createProduct(input:$input){ id name }
        }"""
        return self.gql(q, {"input": input_data})["createProduct"]

    def update_product(self, product_id: str, input_data: dict) -> dict:
        q = """
        mutation($id: ID!, $input: UpdateProductInput!){
          updateProduct(id:$id, input:$input){ id name }
        }"""
        return self.gql(q, {"id": product_id, "input": input_data})["updateProduct"]

    def attach_categories(self, product_id: str, category_ids: list[str]) -> None:
        q = """
        mutation($ids:[ID!]!, $cats:[ID!]!){
          productsBulkAttachCategories(ids:$ids, categoryIds:$cats){ jobId queuedCount message }
        }"""
        self.gql(q, {"ids": [product_id], "cats": category_ids})

    def deactivate_products(self, product_ids: list[str]) -> None:
        q = """
        mutation($ids:[ID!]!){
          productsBulkSetActive(ids:$ids, active:no){ jobId queuedCount message }
        }"""
        self.gql(q, {"ids": product_ids})

    # ---------- images ----------

    def upload_product_image_from_url(
        self, product_id: str, image_url: str,
        name: str | None = None, sort_order: int = 0, set_primary: bool = False,
    ) -> dict:
        with urllib.request.urlopen(image_url, timeout=self.timeout) as r:
            raw = r.read()
        b64 = base64.b64encode(raw).decode()
        ext = image_url.rsplit(".", 1)[-1].split("?")[0].lower() or "jpg"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
        data_uri = f"data:image/{mime};base64,{b64}"
        q = """
        mutation($pid:ID!, $input: UploadProductImageInput!){
          uploadProductImage(productId:$pid, input:$input){ id }
        }"""
        inp = {"image_data": data_uri, "sort_order": sort_order, "set_primary": set_primary}
        if name:
            inp["name"] = name[:120]
        return self.gql(q, {"pid": product_id, "input": inp})["uploadProductImage"]
