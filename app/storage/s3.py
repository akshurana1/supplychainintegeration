"""
S3 storage abstraction.

Supports two modes:
- **AWS mode**: uses boto3 to write to a real (or LocalStack) S3 bucket.
- **Local mode**: writes to the local filesystem, preserving the key
  hierarchy as a directory structure.  This lets the app run and be
  tested without any AWS emulator.

The caller controls which mode via ``Settings.USE_LOCAL_STORAGE``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional, Union, Dict, Any

import boto3

from app.config import Settings, get_settings
from app.observability import get_logger

logger = get_logger(__name__)


class S3Storage:
    """Unified S3 / local filesystem storage for catalogue exports."""

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._use_local = self._settings.USE_LOCAL_STORAGE
        self._bucket = self._settings.S3_BUCKET

        if not self._use_local:
            kwargs: Dict[str, Any] = {"region_name": self._settings.AWS_REGION}
            if self._settings.AWS_ENDPOINT_URL:
                kwargs["endpoint_url"] = self._settings.AWS_ENDPOINT_URL
            self._s3 = boto3.client("s3", **kwargs)
        else:
            self._local_root = Path(self._settings.EXPORT_DIR)

    # ── Write operations ─────────────────────────────────────────────

    def upload_file(self, local_path: Union[str, Path], key: str) -> str:
        """Upload a local file to S3 (or the local store).

        Args:
            local_path: Path to the file on the local filesystem.
            key: S3 object key (e.g. ``exports/<run_id>/catalogue.csv``).

        Returns:
            The S3 URI (``s3://bucket/key``) or local path.
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Source file not found: {local_path}")

        if self._use_local:
            dest = self._local_root / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(local_path), str(dest))
            logger.info("s3_upload_local", key=key, dest=str(dest))
            return str(dest)

        self._s3.upload_file(str(local_path), self._bucket, key)
        uri = f"s3://{self._bucket}/{key}"
        logger.info("s3_upload", bucket=self._bucket, key=key)
        return uri

    # ── Read operations ──────────────────────────────────────────────

    def download_file(self, key: str, local_path: Union[str, Path]) -> Path:
        """Download an S3 object to a local file.

        Args:
            key: S3 object key.
            local_path: Destination path on the local filesystem.

        Returns:
            Path to the downloaded file.
        """
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if self._use_local:
            src = self._local_root / key
            if not src.exists():
                raise FileNotFoundError(f"Object not found in local store: {src}")
            shutil.copy2(str(src), str(local_path))
            return local_path

        self._s3.download_file(self._bucket, key, str(local_path))
        logger.info("s3_download", bucket=self._bucket, key=key)
        return local_path

    def get_file_path(self, key: str) -> Path:
        """Return the local path for a key (local mode only, for testing)."""
        if self._use_local:
            return self._local_root / key
        raise RuntimeError("get_file_path is only available in local mode")
