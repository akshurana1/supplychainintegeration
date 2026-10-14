"""
Product Information Management (PIM) API client.

Key improvements over the original implementation:
1. **Generator-based pagination** – yields one page at a time instead of
   accumulating all products in memory.  At 1 M products × ~200 bytes
   each, the old approach would require ~200 MB of RAM just for the list.
2. **Proper retry with exponential backoff** – via ``ResilientClient``.
3. **Rate limiting** – enforces the documented 10 req/s limit.
4. **Resume support** – the caller can supply a ``start_page`` to resume
   an interrupted export from the last successfully fetched page.
"""

from __future__ import annotations

from typing import Generator, Optional, Dict, Any

from app.config import Settings, get_settings
from app.clients import ResilientClient
from app.observability import get_logger

logger = get_logger(__name__)

# Backward compatibility with existing tests
PAGE_SIZE = get_settings().PAGE_SIZE


class ProductAPIClient:
    """Client for the PIM product catalogue API."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._client = ResilientClient(
            base_url=self._settings.PRODUCT_API_URL,
            api_key=self._settings.PRODUCT_API_KEY,
            max_rps=self._settings.PRODUCT_API_MAX_RPS,
            max_retries=self._settings.MAX_RETRIES,
            base_delay=self._settings.RETRY_BASE_DELAY,
            max_delay=self._settings.RETRY_MAX_DELAY,
        )

    def fetch_pages(
        self,
        *,
        start_page: int = 1,
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield one page response at a time from the PIM API.

        Each yielded dict contains the full API response:
        ``{"products": [...], "page": N, "total": T, "has_next": bool, ...}``

        This is a *generator* so the caller controls memory: products can
        be written to CSV row-by-row without ever holding the entire
        catalogue in memory.

        Args:
            start_page: Page number to begin fetching from.  Used for
                resuming an interrupted export.

        Yields:
            Page response dicts from the PIM API.
        """
        page = start_page
        page_size = self._settings.PAGE_SIZE

        while True:
            logger.info("pim_fetch_page", page=page, page_size=page_size)

            data = self._client.get(
                "/products",
                params={"page": page, "page_size": page_size},
            )

            products = data.get("products", [])
            total = data.get("total", 0)
            has_next = data.get("has_next", False)

            logger.info(
                "pim_page_fetched",
                page=page,
                products_in_page=len(products),
                total=total,
                has_next=has_next,
            )

            yield data

            if not has_next:
                break
            page = data.get("next_page", page + 1)

    def fetch_all_products(self) -> Generator[Dict[str, Any], None, None]:
        """Yield individual product dicts across all pages.

        Convenience wrapper around :meth:`fetch_pages` for callers that
        want a flat stream of products.
        """
        for page_data in self.fetch_pages():
            yield from page_data.get("products", [])

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
