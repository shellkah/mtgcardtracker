from __future__ import annotations

from decimal import Decimal
from urllib.parse import urljoin

from app.adapters.base import AdapterError, CardRequest, Offer
from app.adapters.shopify_parse import (
    normalize_name,
    parse_condition,
    parse_finish,
    parse_printing_hint,
)
from app.sets import SetResolver


class ShopifyAdapter:
    """Base adapter for stores running on Shopify.

    The storefront ``search/suggest.json`` endpoint lists matching products but
    never includes variants. Variant data (condition / finish / price /
    availability) lives on the public ``products/{handle}.js`` endpoint, so it
    is fetched once per name-matching product.
    """

    needs_browser = False

    def __init__(self, config, http, sets=None):
        self.store_id = config.id
        self.store_name = config.name
        self.zone = config.zone
        self.base_url = config.base_url.rstrip("/")
        self._config = config
        self._http = http
        self._sets = sets or SetResolver(http)

    @property
    def search_url(self) -> str:
        return f"{self.base_url}/search/suggest.json"

    def product_url(self, handle: str) -> str:
        return f"{self.base_url}/products/{handle}.js"
    def fetch_products(self, query: str) -> list[dict]:
        params = {
            "q": query,
            "resources[type]": "product",
            "resources[limit]": "10",
            "resources[options][unavailable_products]": "last",
        }
        try:
            payload = self._http.get_json(self.search_url, params=params)
        except Exception as exc:  # HttpError and others bubble as AdapterError
            raise AdapterError(f"{self.store_id}: search failed for {query!r}: {exc}") from exc
        if not isinstance(payload, dict):
            return []
        resources = payload.get("resources")
        results = resources.get("results") if isinstance(resources, dict) else None
        products = results.get("products") if isinstance(results, dict) else None
        return products if isinstance(products, list) else []

    def fetch_variants(self, handle: str) -> dict:
        url = self.product_url(handle)
        try:
            payload = self._http.get_json(url)
        except Exception as exc:
            raise AdapterError(
                f"{self.store_id}: variants failed for {handle!r}: {exc}"
            ) from exc
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _absolute_image(*sources: dict) -> str | None:
        """Return the first usable product image as an absolute https URL.

        Shopify serves ``//cdn.shopify.com/...`` protocol-relative URLs, which
        resolve incorrectly inside a page loaded over a custom scheme, so the
        scheme is always pinned to https.
        """
        for source in sources:
            for key in ("featured_image", "image"):
                raw = source.get(key)
                if isinstance(raw, str) and raw.strip():
                    return "https:" + raw.strip() if raw.startswith("//") else raw.strip()
        return None

    def _product_to_offers(self, product: dict, variants_product: dict) -> list[Offer]:
        title = variants_product.get("title") or ""
        handle = variants_product.get("handle") or product.get("handle") or ""
        url = product.get("url") or f"/products/{handle}"
        absolute = urljoin(self.base_url + "/", url)
        image_url = self._absolute_image(variants_product, product)
        printing_hint = parse_printing_hint(title, self._sets.code_for)
        offers: list[Offer] = []
        for variant in variants_product.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            variant_title = variant.get("title") or ""
            combined = f"{title} {variant_title}"
            try:
                # Shopify reports prices as integer cents.
                price = Decimal(str(variant.get("price"))) / 100
            except Exception:
                # null / unparseable price: skip this variant, keep the product
                continue
            finish = parse_finish(combined)
            if finish == "unknown":
                # A variant with no finish keyword is the store's plain
                # (non-foil) listing.
                finish = "nonfoil"
            offers.append(Offer(
                store_id=self.store_id,
                store_name=self.store_name,
                title=title,
                price_sgd=price,
                currency="SGD",
                condition=parse_condition(combined),
                finish=finish,
                in_stock=bool(variant.get("available", False)),
                url=absolute,
                printing_hint=printing_hint,
                image_url=image_url,
            ))
        return offers

    def search(self, card: CardRequest) -> list[Offer]:
        products = self.fetch_products(card.name)
        wanted = normalize_name(card.name)
        offers: list[Offer] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            if normalize_name(product.get("title") or "") != wanted:
                continue
            handle = product.get("handle") or ""
            try:
                variants_product = self.fetch_variants(handle)
            except Exception:
                # one unavailable product must not abort the whole search
                continue
            try:
                offers.extend(self._product_to_offers(product, variants_product))
            except Exception:
                # one malformed product must not abort the whole search
                continue
        return offers
