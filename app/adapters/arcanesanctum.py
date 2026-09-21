from __future__ import annotations

import re

from app.adapters.shopify import ShopifyAdapter
from app.adapters.shopify_parse import normalize_name

# Arcane Sanctum titles embed the condition and finish in the product name,
# e.g. "Sol Ring — NM Foil [Judge Gift Cards 2005]", with a single
# "Default Title" variant. Strip that suffix before comparing to the wanted
# card name; the shared offer mapping still reads condition / finish from the
# full title.
_CONDITION_SUFFIX_RE = re.compile(
    r"\s*[\u2014\u2013-]\s*(?:NM|LP|MP|HP|DMG)\b.*$",
    re.IGNORECASE,
)


class ArcaneSanctumAdapter(ShopifyAdapter):
    """Arcane Sanctum runs on Shopify with title-keyed conditions."""

    def _product_key(self, title: str) -> str:
        return normalize_name(_CONDITION_SUFFIX_RE.sub("", title))
