from __future__ import annotations

from app.adapters.shopify import ShopifyAdapter


class FlagshipAdapter(ShopifyAdapter):
    """Flagship Games runs on Shopify; base behavior is sufficient for v1."""
