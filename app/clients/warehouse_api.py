"""
Warehouse Management System (WMS) API client.

Key improvements over the original implementation:
1. **Proper retry** – exponential backoff via ``ResilientClient``.
2. **Rate limiting** – enforces the documented 20 req/s limit.
3. **Partial-success handling** – the WMS returns both ``accepted`` and
   ``rejected`` lists in a single response.  This client surfaces both
   so the caller can track and report per-product outcomes.
4. **Structured result types** – returns typed ``BatchResult`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from app.config import Settings, get_settings
from app.clients import ResilientClient
from app.observability import get_logger

logger = get_logger(__name__)


@dataclass
class BatchResult:
    """Result of sending a single batch to the WMS."""

    batch_index: int
    accepted: List[str] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.error is None

    @property
    def total_sent(self) -> int:
        return len(self.accepted) + len(self.rejected)


class WarehouseAPIClient:
    """Client for the WMS batch product ingestion API."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._client = ResilientClient(
            base_url=self._settings.WAREHOUSE_API_URL,
            api_key=self._settings.WAREHOUSE_API_KEY,
            max_rps=self._settings.WAREHOUSE_API_MAX_RPS,
            max_retries=self._settings.MAX_RETRIES,
            base_delay=self._settings.RETRY_BASE_DELAY,
            max_delay=self._settings.RETRY_MAX_DELAY,
        )
        self._batch_size = self._settings.BATCH_SIZE

    def send_batch(
        self,
        products: List[Dict[str, Any]],
        *,
        batch_index: int = 0,
    ) -> BatchResult:
        """Send a single batch of products to the WMS.

        Args:
            products: List of transformed product dicts (max 100).
            batch_index: Numeric identifier for logging and tracking.

        Returns:
            A ``BatchResult`` with per-product accept/reject outcomes.
        """
        if len(products) > self._batch_size:
            raise ValueError(
                f"Batch size {len(products)} exceeds WMS limit of {self._batch_size}"
            )

        logger.info(
            "wms_send_batch",
            batch_index=batch_index,
            batch_size=len(products),
        )

        try:
            data = self._client.post(
                "/products/batch",
                json={"products": products},
            )

            result = BatchResult(
                batch_index=batch_index,
                accepted=data.get("accepted", []),
                rejected=data.get("rejected", []),
            )

            if result.rejected:
                logger.warning(
                    "wms_batch_partial_reject",
                    batch_index=batch_index,
                    accepted_count=len(result.accepted),
                    rejected_count=len(result.rejected),
                    rejected=result.rejected,
                )
            else:
                logger.info(
                    "wms_batch_accepted",
                    batch_index=batch_index,
                    accepted_count=len(result.accepted),
                )

            return result

        except Exception as exc:
            logger.error(
                "wms_batch_failed",
                batch_index=batch_index,
                error=str(exc),
            )
            return BatchResult(
                batch_index=batch_index,
                error=str(exc),
            )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
