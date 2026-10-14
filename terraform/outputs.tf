# ==============================================================================
# Terraform Outputs
# ==============================================================================
# Exposes resource identifiers, ARNs, and endpoints for external integration,
# CI/CD pipelines, and operational monitoring.
# ==============================================================================

# ------------------------------------------------------------------------------
# Orchestration (Step Functions)
# ------------------------------------------------------------------------------
output "state_machine_arn" {
  description = "The Amazon Resource Name (ARN) of the Step Functions catalogue sync state machine"
  value       = aws_sfn_state_machine.pipeline.arn
}

output "state_machine_name" {
  description = "The name of the Step Functions pipeline state machine"
  value       = aws_sfn_state_machine.pipeline.name
}

# ------------------------------------------------------------------------------
# Storage (S3 & DynamoDB)
# ------------------------------------------------------------------------------
output "s3_bucket_name" {
  description = "Name of the S3 bucket hosting catalogue CSV export objects"
  value       = aws_s3_bucket.catalogue_exports.bucket
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket hosting catalogue CSV export objects"
  value       = aws_s3_bucket.catalogue_exports.arn
}

output "dynamodb_table_runs_name" {
  description = "Name of the DynamoDB table tracking overall sync run lifecycle"
  value       = aws_dynamodb_table.sync_runs.name
}

output "dynamodb_table_runs_arn" {
  description = "ARN of the DynamoDB table tracking overall sync run lifecycle"
  value       = aws_dynamodb_table.sync_runs.arn
}

output "dynamodb_table_items_name" {
  description = "Name of the DynamoDB table storing per-item idempotency state"
  value       = aws_dynamodb_table.sync_items.name
}

output "dynamodb_table_items_arn" {
  description = "ARN of the DynamoDB table storing per-item idempotency state"
  value       = aws_dynamodb_table.sync_items.arn
}

# ------------------------------------------------------------------------------
# Queuing (SQS)
# ------------------------------------------------------------------------------
output "sqs_queue_url" {
  description = "URL of the primary SQS queue delivering batch messages to WMS workers"
  value       = aws_sqs_queue.wms_sync_batches.id
}

output "sqs_queue_arn" {
  description = "ARN of the primary SQS queue delivering batch messages to WMS workers"
  value       = aws_sqs_queue.wms_sync_batches.arn
}

output "sqs_dlq_url" {
  description = "URL of the dead letter queue for failed batch deliveries"
  value       = aws_sqs_queue.wms_sync_batches_dlq.id
}

output "sqs_dlq_arn" {
  description = "ARN of the dead letter queue for failed batch deliveries"
  value       = aws_sqs_queue.wms_sync_batches_dlq.arn
}

# ------------------------------------------------------------------------------
# Compute (Lambda Functions & ECS)
# ------------------------------------------------------------------------------
output "lambda_chunk_and_enqueue_arn" {
  description = "ARN of the chunk-and-enqueue Lambda function"
  value       = aws_lambda_function.chunk_and_enqueue.arn
}

output "lambda_sync_batch_arn" {
  description = "ARN of the sync-batch worker Lambda function"
  value       = aws_lambda_function.sync_batch.arn
}

output "lambda_check_sync_status_arn" {
  description = "ARN of the check-sync-status Lambda function"
  value       = aws_lambda_function.check_sync_status.arn
}

output "lambda_finalize_run_arn" {
  description = "ARN of the finalize-run Lambda function"
  value       = aws_lambda_function.finalize_run.arn
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster executing catalogue export tasks"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster executing catalogue export tasks"
  value       = aws_ecs_cluster.main.arn
}

output "ecs_task_definition_arn" {
  description = "ARN of the ECS task definition for catalogue export"
  value       = aws_ecs_task_definition.export_catalogue.arn
}

# ------------------------------------------------------------------------------
# Security & Observability (Secrets Manager & SNS)
# ------------------------------------------------------------------------------
output "secrets_product_api_key_arn" {
  description = "ARN of the Secrets Manager secret storing the PIM API authentication key"
  value       = aws_secretsmanager_secret.product_api_key.arn
}

output "secrets_warehouse_api_key_arn" {
  description = "ARN of the Secrets Manager secret storing the WMS API authentication key"
  value       = aws_secretsmanager_secret.warehouse_api_key.arn
}

output "sns_alert_topic_arn" {
  description = "ARN of the SNS topic for operational failure and metric alerts"
  value       = aws_sns_topic.pipeline_alerts.arn
}
