# ==============================================================================
# S3 Storage for Catalogue Exports
# ==============================================================================
# Stores intermediate CSV exports produced by the PIM export phase.
# Provides immutable, versioned, encrypted storage with a 90-day retention
# lifecycle policy satisfying operational audit requirements.
# ==============================================================================

resource "aws_s3_bucket" "catalogue_exports" {
  bucket        = "${local.name_prefix}-catalogue-exports-${data.aws_caller_identity.current.account_id}"
  force_destroy = false

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-catalogue-exports"
    Description = "Storage for daily catalogue CSV exports"
  })
}

# Enforce bucket versioning to guard against accidental deletion or overwrite
resource "aws_s3_bucket_versioning" "catalogue_exports" {
  bucket = aws_s3_bucket.catalogue_exports.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Server-Side Encryption using SSE-S3 (AES-256)
resource "aws_s3_bucket_server_side_encryption_configuration" "catalogue_exports" {
  bucket = aws_s3_bucket.catalogue_exports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Block all public access at the bucket level
resource "aws_s3_bucket_public_access_block" "catalogue_exports" {
  bucket = aws_s3_bucket.catalogue_exports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle management: expire objects after configurable retention days (default 90)
# and clean up non-current versions after 30 days to optimize storage costs.
resource "aws_s3_bucket_lifecycle_configuration" "catalogue_exports" {
  bucket = aws_s3_bucket.catalogue_exports.id

  rule {
    id     = "expire-catalogue-exports-after-retention"
    status = "Enabled"

    filter {
      prefix = "exports/"
    }

    expiration {
      days = var.s3_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "clean-up-root-artifacts"
    status = "Enabled"

    filter {}

    expiration {
      days = var.s3_retention_days
    }
  }
}
