from __future__ import annotations

from app.adapters.shopify import ShopifyAdapter


class MtgAsiaAdapter(ShopifyAdapter):
    """MTG Asia runs on Shopify; base behavior is sufficient for v1."""
