# ==============================================================================
# DynamoDB State and Idempotency Store
# ==============================================================================
# Provides distributed state tracking, checkpointing, and idempotency guarantees
# across runs and batches.
#
# Design Rationale:
# 1. PAY_PER_REQUEST billing accommodates bursty daily scheduled workloads without
#    paying for idle provisioned throughput.
# 2. sync_runs tracks run lifecycle (STARTED, EXPORTING, SYNCING, COMPLETED, FAILED).
#    Point-in-Time Recovery (PITR) is enabled for disaster recovery and operational auditing.
# 3. sync_items uses composite key `run_sku` (run_id#sku) as partition key for O(1)
#    deduplication checks before dispatching batches to WMS ("never send twice").
# 4. GSI on `run_id` allows efficient querying of all item statuses during finalization.
# 5. DynamoDB TTL automatically purges historical item records to manage storage growth.
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Sync Runs Table
# ------------------------------------------------------------------------------
resource "aws_dynamodb_table" "sync_runs" {
  name         = "${local.name_prefix}-sync-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"

  attribute {
    name = "run_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-sync-runs"
    Description = "Tracks pipeline run statuses, metrics, and checkpoints"
  })
}

# ------------------------------------------------------------------------------
# 2. Sync Items Table (Deduplication & Item Tracking)
# ------------------------------------------------------------------------------
resource "aws_dynamodb_table" "sync_items" {
  name         = "${local.name_prefix}-sync-items"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_sku"

  attribute {
    name = "run_sku"
    type = "S"
  }

  attribute {
    name = "run_id"
    type = "S"
  }

  # Global Secondary Index: Enables batch query of all items for a specific run_id
  global_secondary_index {
    name            = "run_id-index"
    hash_key        = "run_id"
    projection_type = "ALL"
  }

  # TTL configuration: Automatically delete item records when TTL timestamp expires
  # (typically set to 30-90 days after creation)
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-sync-items"
    Description = "Per-item status and idempotency state for WMS synchronization"
  })
}
