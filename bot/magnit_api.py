"""Magnit API client for fetching promotions per store."""

import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Page size used by magnit.ru itself
PAGE_SIZE = 36

# Max pages fetched per store per poll
MAX_PAGES = 3


class MagnitApiError(Exception):
    """Raised when the Magnit API returns an error."""


class MagnitClient:
    """Client for Magnit webgate API endpoints.

    Mirrors the requests the magnit.ru promotions page sends to
    ``POST /webgate/v2/goods/search`` (items sorted by discount).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str = "https://magnit.ru",
        timeout: int = 30,
    ) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://magnit.ru",
            "Referer": "https://magnit.ru/",
            "x-client-name": "magnit",
            "x-app-version": "2026.11.9-10.3",
            "x-device-platform": "Web",
            "x-magnit-bff": "1",
        }

    # ------------------------------------------------------------------
    # Promotions
    # ------------------------------------------------------------------

    async def get_promotions_for_store(
        self,
        store_code: str,
        limit: int = PAGE_SIZE,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Fetch discounted goods for a given store code.

        Uses ``POST /webgate/v2/goods/search`` sorted by discount, the same
        endpoint the magnit.ru promotions page uses. Returns a list of
        normalised promotion dicts.
        """
        promotions: List[Dict[str, Any]] = []

        for page in range(MAX_PAGES):
            page_offset = offset + page * PAGE_SIZE
            data = await self._search_goods_page(store_code, limit=PAGE_SIZE, offset=page_offset)
            if data is None:
                break

            items = data.get("items") or []
            if not isinstance(items, list):
                break

            for item in items:
                promo = self._normalize_product(item)
                if promo:
                    promotions.append(promo)

            if len(items) < PAGE_SIZE:
                break

            if len(promotions) >= limit:
                break

        return promotions

    async def _search_goods_page(
        self, store_code: str, limit: int, offset: int
    ) -> Optional[Dict[str, Any]]:
        """One page of the goods search request."""
        url = f"{self.base_url}/webgate/v2/goods/search"

        payload = {
            "includeAdultGoods": True,
            "pagination": {"offset": offset, "limit": limit},
            "sort": {"order": "desc", "type": "discount"},
            "term": "",
            "storeCode": store_code,
            "storeType": "dostavka",
            "catalogType": "2",
        }

        try:
            async with self.session.post(
                url, json=payload, headers=self._headers(), timeout=self.timeout
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.warning(
                        "Magnit API returned %s for store %s: %s",
                        response.status,
                        store_code,
                        body[:200],
                    )
                    return None
                return await response.json(content_type=None)
        except aiohttp.ClientError as exc:
            logger.error("Network error fetching promotions for store %s: %s", store_code, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error fetching promotions for store %s", store_code)
            return None

    async def get_promotion_categories(self, store_code: str) -> List[Dict[str, Any]]:
        """Fetch promotion categories for a store."""
        url = f"{self.base_url}/webgate/v2/promotions/categories"
        params = {"storeCode": store_code}

        try:
            async with self.session.get(
                url, params=params, headers=self._headers(), timeout=self.timeout
            ) as response:
                if response.status != 200:
                    return []
                data = await response.json(content_type=None)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error fetching promotion categories: %s", exc)
            return []

        return data.get("categories", []) or data.get("items", []) or []

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_product(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalise a raw goods/search item into a promotion dict."""
        try:
            product_id = item.get("id") or item.get("productId") or item.get("sku")
            if not product_id:
                return None

            promo = item.get("promotion") or {}
            if not promo.get("isPromotion", False):
                return None

            discount_percent = promo.get("discountPercent") or 0

            # Prices come in kopecks; convert to rubles
            def kopecks_to_rubles(value: Any) -> Optional[float]:
                if value is None:
                    return None
                try:
                    return round(float(value) / 100, 2)
                except (ValueError, TypeError):
                    return None

            new_price = kopecks_to_rubles(item.get("price"))
            old_price = kopecks_to_rubles(promo.get("oldPrice"))

            # Fallback: compute discount from prices
            if not discount_percent and old_price and new_price:
                discount_percent = round((old_price - new_price) / old_price * 100, 1)

            gallery = item.get("gallery") or []
            image_url = ""
            for photo in gallery:
                if isinstance(photo, dict) and photo.get("url"):
                    image_url = photo["url"]
                    break

            name = item.get("name") or item.get("title") or ""

            return {
                "product_id": str(product_id),
                "name": name.strip(),
                "brand": (item.get("brand") or "").strip() if isinstance(item.get("brand"), str) else "",
                "image_url": image_url,
                "discount_percent": float(discount_percent) if discount_percent else 0.0,
                "old_price": old_price,
                "new_price": new_price,
                "category": item.get("category") or "",
                "store_code": item.get("storeCode") or "",
                "store_address": item.get("storeAddress") or "",
            }
        except Exception:  # noqa: BLE001
            logger.exception("Failed to normalise product item")
            return None