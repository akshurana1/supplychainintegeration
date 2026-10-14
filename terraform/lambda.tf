# ==============================================================================
# AWS Lambda Serverless Functions
# ==============================================================================
# Implements the event-driven stages of the sync pipeline:
# 1. chunk-and-enqueue: Streams CSV from S3 and pushes batches of 100 to SQS.
# 2. sync-batch: Worker invoked by SQS, enforces idempotency, calls WMS API.
#    Uses reserved concurrency (5) to guarantee compliance with 20 RPS rate limit.
# 3. check-sync-status: Polled by Step Functions to assess queue drain & completion.
# 4. finalize-run: Aggregates metrics, updates run record, and sends notifications.
# ==============================================================================

# ------------------------------------------------------------------------------
# Package Source Code Archives
# ------------------------------------------------------------------------------
data "archive_file" "chunk_and_enqueue" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/chunk_and_enqueue.py"
  output_path = "${path.module}/dist/chunk_and_enqueue.zip"
}

data "archive_file" "sync_batch" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/sync_batch.py"
  output_path = "${path.module}/dist/sync_batch.zip"
}

data "archive_file" "check_sync_status" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/check_sync_status.py"
  output_path = "${path.module}/dist/check_sync_status.zip"
}

data "archive_file" "finalize_run" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/finalize_run.py"
  output_path = "${path.module}/dist/finalize_run.zip"
}

# ------------------------------------------------------------------------------
# 1. Chunk and Enqueue Lambda
# ------------------------------------------------------------------------------
resource "aws_lambda_function" "chunk_and_enqueue" {
  function_name    = "${local.name_prefix}-chunk-and-enqueue"
  description      = "Reads exported CSV from S3 and enqueues product batches to SQS"
  role             = aws_iam_role.chunk_and_enqueue_role.arn
  runtime          = "python3.11"
  handler          = "chunk_and_enqueue.handler"
  filename         = data.archive_file.chunk_and_enqueue.output_path
  source_code_hash = data.archive_file.chunk_and_enqueue.output_base64sha256
  memory_size      = 512
  timeout          = 300

  environment {
    variables = {
      ENVIRONMENT          = var.environment
      S3_BUCKET            = aws_s3_bucket.catalogue_exports.bucket
      SQS_QUEUE_URL        = aws_sqs_queue.wms_sync_batches.id
      DYNAMODB_TABLE_RUNS  = aws_dynamodb_table.sync_runs.name
      DYNAMODB_TABLE_ITEMS = aws_dynamodb_table.sync_items.name
      BATCH_SIZE           = "100"
    }
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-chunk-and-enqueue"
    Description = "Chunk and enqueue catalogue data"
  })

  depends_on = [aws_cloudwatch_log_group.lambda_chunk_and_enqueue]
}

# ------------------------------------------------------------------------------
# 2. Sync Batch Worker Lambda
# ------------------------------------------------------------------------------
resource "aws_lambda_function" "sync_batch" {
  function_name                  = "${local.name_prefix}-sync-batch"
  description                    = "SQS consumer Lambda that delivers batch updates to WMS with idempotency"
  role                           = aws_iam_role.sync_batch_role.arn
  runtime                        = "python3.11"
  handler                        = "sync_batch.handler"
  filename                       = data.archive_file.sync_batch.output_path
  source_code_hash               = data.archive_file.sync_batch.output_base64sha256
  memory_size                    = 256
  timeout                        = 60
  reserved_concurrent_executions = var.sync_concurrency # Rate limit control (default 5 workers)

  environment {
    variables = {
      ENVIRONMENT                  = var.environment
      WAREHOUSE_API_URL            = var.warehouse_api_url
      WAREHOUSE_API_KEY_SECRET_ARN = aws_secretsmanager_secret.warehouse_api_key.arn
      DYNAMODB_TABLE_RUNS          = aws_dynamodb_table.sync_runs.name
      DYNAMODB_TABLE_ITEMS         = aws_dynamodb_table.sync_items.name
    }
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-sync-batch"
    Description = "Processes batch items to WMS"
  })

  depends_on = [aws_cloudwatch_log_group.lambda_sync_batch]
}

# SQS Trigger for Sync Batch Lambda
resource "aws_lambda_event_source_mapping" "sync_batch_sqs" {
  event_source_arn = aws_sqs_queue.wms_sync_batches.arn
  function_name    = aws_lambda_function.sync_batch.arn
  batch_size       = 1 # Each SQS message already contains a chunk of up to 100 products
  enabled          = true
}

# ------------------------------------------------------------------------------
# 3. Check Sync Status Lambda
# ------------------------------------------------------------------------------
resource "aws_lambda_function" "check_sync_status" {
  function_name    = "${local.name_prefix}-check-sync-status"
  description      = "Polls queue depth and DynamoDB state to evaluate sync completion"
  role             = aws_iam_role.check_sync_status_role.arn
  runtime          = "python3.11"
  handler          = "check_sync_status.handler"
  filename         = data.archive_file.check_sync_status.output_path
  source_code_hash = data.archive_file.check_sync_status.output_base64sha256
  memory_size      = var.lambda_memory
  timeout          = 60

  environment {
    variables = {
      ENVIRONMENT          = var.environment
      SQS_QUEUE_URL        = aws_sqs_queue.wms_sync_batches.id
      SQS_DLQ_URL          = aws_sqs_queue.wms_sync_batches_dlq.id
      DYNAMODB_TABLE_RUNS  = aws_dynamodb_table.sync_runs.name
      DYNAMODB_TABLE_ITEMS = aws_dynamodb_table.sync_items.name
    }
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-check-sync-status"
    Description = "Polls sync progress for Step Functions loop"
  })

  depends_on = [aws_cloudwatch_log_group.lambda_check_sync_status]
}

# ------------------------------------------------------------------------------
# 4. Finalize Run Lambda
# ------------------------------------------------------------------------------
resource "aws_lambda_function" "finalize_run" {
  function_name    = "${local.name_prefix}-finalize-run"
  description      = "Aggregates execution metrics and finalizes sync run state in DynamoDB"
  role             = aws_iam_role.finalize_run_role.arn
  runtime          = "python3.11"
  handler          = "finalize_run.handler"
  filename         = data.archive_file.finalize_run.output_path
  source_code_hash = data.archive_file.finalize_run.output_base64sha256
  memory_size      = var.lambda_memory
  timeout          = 120

  environment {
    variables = {
      ENVIRONMENT          = var.environment
      DYNAMODB_TABLE_RUNS  = aws_dynamodb_table.sync_runs.name
      DYNAMODB_TABLE_ITEMS = aws_dynamodb_table.sync_items.name
      SNS_ALERT_TOPIC_ARN  = aws_sns_topic.pipeline_alerts.arn
    }
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-finalize-run"
    Description = "Finalizes sync run outcomes and alerts"
  })

  depends_on = [aws_cloudwatch_log_group.lambda_finalize_run]
}
