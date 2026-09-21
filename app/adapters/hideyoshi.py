from __future__ import annotations

from app.adapters.shopify import ShopifyAdapter


class HideyoshiAdapter(ShopifyAdapter):
    """Hideyoshi TCG runs on Shopify; base behavior is sufficient for v1."""
