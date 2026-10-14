"""
Catalogue export service — PIM API → CSV → S3.

Streams products from the PIM API page by page, writes them to a CSV
file as they arrive, then uploads the complete CSV to S3.

Key design decisions:
- **Streaming write**: products are written to CSV row-by-row as pages
  arrive.  At no point is the entire catalogue held in memory.
- **Resumable pagination**: the ``last_export_page`` is persisted in the
  run record after each successful page.  If the process crashes, the
  orchestrator can resume from the last page.
- **Deterministic S3 key**: ``exports/{run_id}/catalogue.csv`` so each
  run's export is isolated and retained for 90 days.
"""

from __future__ import annotations

import csv
import os
import tempfile
from typing import Optional

from app.config import Settings, get_settings
from app.clients.product_api import ProductAPIClient
from app.storage.s3 import S3Storage
from app.storage.state import StateStore, RunStatus
from app.observability import get_logger

logger = get_logger(__name__)

# CSV column order — matches the PIM API response fields.
CSV_FIELDNAMES = ["id", "name", "category", "price", "currency", "updated_at"]


def export_catalogue(
    run_id: str,
    *,
    settings: Optional[Settings] = None,
    product_client: Optional[ProductAPIClient] = None,
    s3_storage: Optional[S3Storage] = None,
    state_store: Optional[StateStore] = None,
    start_page: int = 1,
) -> str:
    """Export the full product catalogue to CSV and upload to S3.

    Args:
        run_id: Unique identifier for this sync run.
        settings: Application settings (injected for testing).
        product_client: PIM API client (injected for testing).
        s3_storage: S3 storage (injected for testing).
        state_store: DynamoDB state store (injected for testing).
        start_page: Page to resume from (for interrupted exports).

    Returns:
        The S3 key of the uploaded CSV file.
    """
    settings = settings or get_settings()
    product_client = product_client or ProductAPIClient(settings)
    s3_storage = s3_storage or S3Storage(settings)
    state_store = state_store or StateStore(settings)

    s3_key = f"exports/{run_id}/catalogue.csv"

    state_store.update_run(run_id, status=RunStatus.EXPORTING)

    # Use a temp file so we get atomic upload — the file is only
    # uploaded to S3 once it is completely written.
    os.makedirs(settings.EXPORT_DIR, exist_ok=True)
    local_path = os.path.join(settings.EXPORT_DIR, f"{run_id}_catalogue.csv")

    total_products = 0

    try:
        with open(local_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()

            for page_data in product_client.fetch_pages(start_page=start_page):
                products = page_data.get("products", [])
                for product in products:
                    writer.writerow(product)
                    total_products += 1

                # Persist progress so we can resume on crash.
                current_page = page_data.get("page", start_page)
                state_store.update_run(
                    run_id,
                    last_export_page=current_page,
                    total_products=total_products,
                )

                logger.info(
                    "export_page_written",
                    run_id=run_id,
                    page=current_page,
                    total_products_so_far=total_products,
                )

        # Upload to S3.
        s3_uri = s3_storage.upload_file(local_path, s3_key)

        state_store.update_run(
            run_id,
            status=RunStatus.EXPORT_COMPLETE,
            s3_key=s3_key,
            total_products=total_products,
        )

        logger.info(
            "export_complete",
            run_id=run_id,
            s3_key=s3_key,
            s3_uri=s3_uri,
            total_products=total_products,
        )

        return s3_key

    except Exception:
        state_store.update_run(run_id, status=RunStatus.FAILED)
        raise
    finally:
        product_client.close()
