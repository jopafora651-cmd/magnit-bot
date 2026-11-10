"""Store search module for Magnit - finds store codes by address/name.

Flow:
1. Geocode a free-form query (city / street / address) via Nominatim (OSM).
2. Query the Magnit ``stores-facade`` API with a bounding box around the
   geocoded point (same endpoint the magnit.ru/shops map page uses).
3. Return the nearest active stores with their Magnit store codes.
"""

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# All Magnit store formats shown on the magnit.ru/shops page
STORE_TYPES = ["MM", "GM", "DG", "MO", "ME", "MC", "DARKSTORE", "MM_MINI", "ZARYAD"]

# Max pages of 50 stores fetched per search
MAX_PAGES = 3

# Max half-size of a search box in degrees (avoid whole-country queries)
MAX_BOX_HALF_DEG = 0.4

# Min half-size of a search box in degrees (~2-3 km) so street-level
# geocoding results still cover nearby stores
MIN_BOX_HALF_LAT = 0.02
MIN_BOX_HALF_LON = 0.03


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points in kilometres."""
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class StoreSearcher:
    """Searches for Magnit stores by address or city name."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str = "https://magnit.ru") -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")

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
    # Geocoding
    # ------------------------------------------------------------------

    async def geocode(self, query: str) -> Optional[Dict[str, Any]]:
        """Geocode a query via Nominatim.

        Returns ``{"lat": ..., "lon": ..., "bbox": (south, north, west, east)}``
        or ``None`` when nothing is found.
        """
        params = {"q": query, "format": "json", "limit": 1, "countrycodes": "ru"}
        headers = {
            "Accept": "application/json",
            "User-Agent": "magnit-promo-bot/1.0 (telegram store search)",
        }
        try:
            async with self.session.get(NOMINATIM_URL, params=params, headers=headers) as response:
                if response.status != 200:
                    logger.warning("Nominatim returned %s for %r", response.status, query)
                    return None
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.error("Geocoding network error for %r: %s", query, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.exception("Geocoding failed for %r", query)
            return None

        if not data:
            return None
        try:
            first = data[0]
            bbox = [float(x) for x in first.get("boundingbox", [])]
            return {
                "lat": float(first["lat"]),
                "lon": float(first["lon"]),
                "bbox": bbox,  # [south, north, west, east]
            }
        except (KeyError, ValueError, TypeError, IndexError):
            return None

    # ------------------------------------------------------------------
    # Store search
    # ------------------------------------------------------------------

    async def search_stores(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search for Magnit stores near the address in ``query``.

        Returns a list of dicts (nearest first) with keys:
        ``code``, ``address``, ``store_type``, ``lat``, ``lon``,
        ``distance_km``.
        """
        geo = await self.geocode(query)
        if not geo:
            return []

        lat, lon = geo["lat"], geo["lon"]
        box = self._search_box(geo)
        all_stores = await self._fetch_stores_in_box(box)
        if not all_stores:
            return []

        # Keep active stores, compute distance to the query point
        stores: List[Dict[str, Any]] = []
        for s in all_stores:
            if not s.get("isActive", False):
                continue
            code = (s.get("externalId") or {}).get("storeCode")
            if not code:
                continue
            coords = s.get("coordinates") or {}
            s_lat = coords.get("latitude")
            s_lon = coords.get("longitude")
            stores.append(
                {
                    "code": str(code),
                    "address": s.get("address") or "",
                    "store_type": s.get("storeTypeV2") or "",
                    "lat": s_lat,
                    "lon": s_lon,
                    "distance_km": (
                        round(_haversine_km(lat, lon, s_lat, s_lon), 1)
                        if s_lat is not None and s_lon is not None
                        else None
                    ),
                }
            )

        stores.sort(key=lambda s: s["distance_km"] if s["distance_km"] is not None else 1e9)
        return stores[:limit]

    async def _fetch_stores_in_box(self, box: List[float]) -> List[Dict[str, Any]]:
        """Fetch all stores inside a bbox [south, north, west, east]."""
        south, north, west, east = box
        base_payload: Dict[str, Any] = {
            "filters": {
                "geo": {
                    "typeName": "box",
                    "leftTopPoint": {"latitude": north, "longitude": west},
                    "rightBottomPoint": {"latitude": south, "longitude": east},
                },
                "storeTypeListV2": STORE_TYPES,
            },
            "sorting": {"sortBy": "SORT_BY_CITY", "sortType": "SORT_TYPE_ASC"},
        }

        url = f"{self.base_url}/webgate/v1/stores-facade/search/detail"
        collected: List[Dict[str, Any]] = []
        for page in range(MAX_PAGES):
            payload = dict(base_payload)
            payload["pagination"] = {"offset": page * 50, "size": 50}
            try:
                async with self.session.post(
                    url, json=payload, headers=self._headers()
                ) as response:
                    if response.status != 200:
                        logger.warning(
                            "stores-facade/search/detail returned %s (page %s)",
                            response.status,
                            page,
                        )
                        break
                    data = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("Store search network error (page %s): %s", page, exc)
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Store search failed (page %s)", page)
                break

            items = data.get("data") or []
            collected.extend(items)
            total = data.get("totalCount") or len(collected)
            if len(collected) >= total or not items:
                break
        return collected

    @staticmethod
    def _search_box(geo: Dict[str, Any]) -> List[float]:
        """Build a search box from geocode result, clamped to sane size."""
        lat, lon = geo["lat"], geo["lon"]
        bbox = geo.get("bbox") or []
        if len(bbox) == 4:
            south, north, west, east = bbox
        else:
            south, north, west, east = lat - 0.02, lat + 0.02, lon - 0.03, lon + 0.03

        def clamp_max(value: float, center: float) -> float:
            if abs(value - center) > MAX_BOX_HALF_DEG:
                return center + (MAX_BOX_HALF_DEG if value > center else -MAX_BOX_HALF_DEG)
            return value

        def expand_min(value_low: float, value_high: float, center: float, half: float):
            if center - value_low < half:
                value_low = center - half
            if value_high - center < half:
                value_high = center + half
            return value_low, value_high

        north = clamp_max(north, lat)
        south = clamp_max(south, lat)
        east = clamp_max(east, lon)
        west = clamp_max(west, lon)

        south, north = expand_min(south, north, lat, MIN_BOX_HALF_LAT)
        west, east = expand_min(west, east, lon, MIN_BOX_HALF_LON)
        return [south, north, west, east]