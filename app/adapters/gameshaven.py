from __future__ import annotations

from app.adapters.shopify import ShopifyAdapter


class GamesHavenAdapter(ShopifyAdapter):
    """Games Haven runs on Shopify; base behavior is sufficient for v1."""
