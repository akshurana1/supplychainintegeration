"""
Orchestrator — entry point for the PIM-to-WMS synchronisation pipeline.

This module ties together the export and sync phases into a single
run.  In production, this logic would be orchestrated by AWS Step
Functions (see ARCHITECTURE.md); locally it runs as a Python script.

Execution flow:
1. Generate a unique run ID.
2. Create a run record in the state store.
3. **Export phase**: paginate PIM → stream CSV → upload to S3.
4. **Sync phase**: read CSV → transform → batch-send to WMS.
5. Finalise the run record with outcome summary.

Resume capability:
    If invoked with ``--resume <run_id>``, the orchestrator looks up
    the run record, determines which phase was last completed, and
    resumes from there (e.g. re-exporting from the last page, or
    re-syncing with dedup skipping already-processed products).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from typing import Optional

import structlog

from app.config import get_settings
from app.observability import setup_logging, get_logger
from app.storage.state import StateStore, RunStatus
from app.services.catalogue_export import export_catalogue
from app.services.warehouse_sync import sync_catalogue, SyncSummary


def generate_run_id() -> str:
    """Generate a unique, time-sortable run identifier.

    Format: ``YYYYMMDD-HHMMSS-<uuid4_short>`` for human readability
    plus uniqueness.
    """
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    short_uuid = uuid.uuid4().hex[:8]
    return f"{ts}-{short_uuid}"


def run(
    *,
    resume_run_id: Optional[str] = None,
) -> SyncSummary:
    """Execute the full PIM → WMS synchronisation pipeline.

    Args:
        resume_run_id: If provided, resume a previously interrupted run
            instead of starting fresh.

    Returns:
        A ``SyncSummary`` with the outcome of the sync phase.
    """
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    logger = get_logger("orchestrator")

    state_store = StateStore(settings)

    # ── Determine run ID and phase ───────────────────────────────────
    if resume_run_id:
        run_id = resume_run_id
        existing = state_store.get_run(run_id)
        if not existing:
            logger.error("resume_run_not_found", run_id=run_id)
            raise ValueError(f"Run {run_id} not found — cannot resume")
        logger.info("resuming_run", run_id=run_id, current_status=existing.get("status"))
    else:
        run_id = generate_run_id()
        state_store.create_run(run_id)
        logger.info("new_run_started", run_id=run_id)

    # Bind run_id to all subsequent log entries via contextvars.
    structlog.contextvars.bind_contextvars(run_id=run_id)

    try:
        # ── Phase 1: Export ──────────────────────────────────────────
        existing = state_store.get_run(run_id)
        current_status = existing.get("status", RunStatus.STARTED.value) if existing else RunStatus.STARTED.value

        if current_status in (
            RunStatus.STARTED.value,
            RunStatus.EXPORTING.value,
        ):
            # Determine resume page.
            start_page = 1
            if current_status == RunStatus.EXPORTING.value and existing:
                last_page = existing.get("last_export_page", 0)
                if last_page > 0:
                    start_page = last_page + 1
                    logger.info("export_resuming", start_page=start_page)

            s3_key = export_catalogue(
                run_id,
                settings=settings,
                state_store=state_store,
                start_page=start_page,
            )
            # Ensure run status is marked as EXPORT_COMPLETE
            state_store.update_run(
                run_id,
                status=RunStatus.EXPORT_COMPLETE,
                s3_key=s3_key,
            )
            logger.info("export_phase_done", s3_key=s3_key)
        else:
            # Export already completed — read S3 key from run record.
            s3_key = existing.get("s3_key") if existing else None
            if not s3_key:
                raise RuntimeError(
                    f"Run {run_id} has status {current_status} but no s3_key"
                )
            logger.info("export_already_complete", s3_key=s3_key)

        # ── Phase 2: Sync ────────────────────────────────────────────
        current_status = (state_store.get_run(run_id) or {}).get("status")
        if current_status in (
            RunStatus.EXPORT_COMPLETE.value,
            RunStatus.SYNCING.value,
            RunStatus.FAILED.value,
        ):
            summary = sync_catalogue(
                run_id,
                s3_key,
                settings=settings,
                state_store=state_store,
            )
        elif current_status == RunStatus.COMPLETED.value:
            logger.info("sync_already_complete", run_id=run_id)
            summary = SyncSummary(run_id=run_id)
        else:
            raise RuntimeError(
                f"Unexpected run status for sync phase: {current_status}"
            )

        # ── Phase 3: Report ──────────────────────────────────────────
        logger.info(
            "pipeline_complete",
            run_id=run_id,
            total_products=summary.total_products,
            accepted=summary.total_accepted,
            rejected=summary.total_rejected,
            failed=summary.total_failed,
            skipped_dedup=summary.total_skipped_dedup,
            success=summary.is_success,
        )

        if summary.rejected_details:
            logger.warning(
                "pipeline_rejections",
                run_id=run_id,
                count=len(summary.rejected_details),
                details=summary.rejected_details[:20],  # Log first 20 only.
            )

        if summary.failed_batches:
            logger.error(
                "pipeline_failed_batches",
                run_id=run_id,
                count=len(summary.failed_batches),
                batch_indices=summary.failed_batches,
            )

        return summary

    except Exception as exc:
        logger.error("pipeline_failed", run_id=run_id, error=str(exc), exc_info=True)
        state_store.update_run(
            run_id,
            status=RunStatus.FAILED,
            error=str(exc),
            completed_at=time.time(),
        )
        raise


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="PIM → WMS catalogue synchronisation pipeline",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="Resume a previously interrupted run by its run ID",
    )
    args = parser.parse_args()

    try:
        summary = run(resume_run_id=args.resume)
        if not summary.is_success:
            sys.exit(1)
    except Exception:
        sys.exit(2)


if __name__ == "__main__":
    main()
