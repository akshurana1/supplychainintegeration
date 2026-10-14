"""
Application configuration with validation.

Uses pydantic-settings for type-safe, environment-driven configuration.
All settings are overridable via environment variables for local dev,
CI, and production (injected via ECS task definition / Lambda env).
"""

from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Centralised, validated application settings."""

    model_config = {"env_prefix": "", "case_sensitive": True}

    # ── PIM API ──────────────────────────────────────────────────────────
    PRODUCT_API_URL: str = Field(
        default="http://localhost:8001",
        description="Base URL for the Product Information Management API",
    )
    PRODUCT_API_KEY: str = Field(
        default="challenge-product-key",
        description="API key for PIM authentication",
    )
    PRODUCT_API_MAX_RPS: float = Field(
        default=10.0,
        description="Maximum requests per second to the PIM API",
    )
    PAGE_SIZE: int = Field(
        default=500,
        ge=1,
        le=500,
        description="Number of products per page when paginating the PIM API",
    )

    # ── WMS API ──────────────────────────────────────────────────────────
    WAREHOUSE_API_URL: str = Field(
        default="http://localhost:8002",
        description="Base URL for the Warehouse Management System API",
    )
    WAREHOUSE_API_KEY: str = Field(
        default="challenge-warehouse-key",
        description="API key for WMS authentication",
    )
    WAREHOUSE_API_MAX_RPS: float = Field(
        default=20.0,
        description="Maximum sustained requests per second to the WMS API",
    )
    BATCH_SIZE: int = Field(
        default=100,
        ge=1,
        le=100,
        description="Number of products per WMS batch",
    )

    # ── AWS / Storage ────────────────────────────────────────────────────
    AWS_REGION: str = Field(default="ap-south-1")
    AWS_ENDPOINT_URL: Optional[str] = Field(
        default=None,
        description="Override endpoint for LocalStack or other local emulators",
    )
    S3_BUCKET: str = Field(
        default="catalogue-exports",
        description="S3 bucket for storing catalogue CSV exports",
    )
    DYNAMODB_TABLE_RUNS: str = Field(
        default="sync_runs",
        description="DynamoDB table for tracking sync run state",
    )
    DYNAMODB_TABLE_ITEMS: str = Field(
        default="sync_items",
        description="DynamoDB table for tracking per-product processing state",
    )
    SQS_QUEUE_NAME: str = Field(
        default="wms-sync-batches",
        description="SQS queue for WMS batch processing",
    )
    SQS_DLQ_NAME: str = Field(
        default="wms-sync-batches-dlq",
        description="SQS dead-letter queue for failed batch processing",
    )

    # ── Retry / resilience ───────────────────────────────────────────────
    MAX_RETRIES: int = Field(
        default=10,
        ge=1,
        description="Maximum number of retries for transient failures",
    )
    RETRY_BASE_DELAY: float = Field(
        default=1.0,
        description="Base delay in seconds for exponential backoff",
    )
    RETRY_MAX_DELAY: float = Field(
        default=60.0,
        description="Maximum delay in seconds between retries",
    )

    # ── Operational ──────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(default="INFO")
    EXPORT_DIR: str = Field(
        default="./runtime/exports",
        description="Local directory for CSV exports (local mode only)",
    )
    USE_LOCAL_STORAGE: bool = Field(
        default=True,
        description="When True, use local filesystem instead of AWS services",
    )
    SYNC_CONCURRENCY: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of concurrent batch workers for WMS sync",
    )


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
