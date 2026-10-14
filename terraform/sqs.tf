# ==============================================================================
# SQS Queues for Asynchronous WMS Batch Dispatching
# ==============================================================================
# Decouples CSV chunking from WMS API submission.
#
# Design Rationale:
# 1. Buffering: Absorbs rate disparities between high-throughput CSV chunking
#    and rate-limited WMS API consumption (max 20 RPS).
# 2. Resiliency: Visibility timeout (300s) allows Lambda batch workers sufficient
#    time for exponential backoff retries against transient 429/5xx responses.
# 3. Dead Letter Queue: Messages failing processing after 3 receive attempts
#    are routed to DLQ without stalling other batches, isolating poison pills.
# 4. SSE-SQS encryption protects sensitive catalogue data at rest.
# ==============================================================================

# ------------------------------------------------------------------------------
# Dead Letter Queue (DLQ)
# ------------------------------------------------------------------------------
resource "aws_sqs_queue" "wms_sync_batches_dlq" {
  name                      = "${local.name_prefix}-wms-sync-batches-dlq"
  message_retention_seconds = 1209600 # 14 days retention for troubleshooting failed batches
  sqs_managed_sse_enabled   = true

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-wms-sync-batches-dlq"
    Description = "Dead-letter queue for failed WMS sync batch messages"
  })
}

# ------------------------------------------------------------------------------
# Primary Processing Queue
# ------------------------------------------------------------------------------
resource "aws_sqs_queue" "wms_sync_batches" {
  name                       = "${local.name_prefix}-wms-sync-batches"
  visibility_timeout_seconds = 300   # 5 minutes, aligned with Lambda execution and retry timeouts
  message_retention_seconds  = 345600 # 4 days retention
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.wms_sync_batches_dlq.arn
    maxReceiveCount     = 3
  })

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-wms-sync-batches"
    Description = "Queue storing chunked product batches for WMS delivery"
  })
}

# Restrict DLQ ingestion permissions strictly to the primary sync queue
resource "aws_sqs_queue_redrive_allow_policy" "wms_sync_batches_dlq_allow" {
  queue_url = aws_sqs_queue.wms_sync_batches_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.wms_sync_batches.arn]
  })
}
