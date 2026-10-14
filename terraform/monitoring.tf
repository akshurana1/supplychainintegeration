# ==============================================================================
# CloudWatch Monitoring, Alarms, and SNS Alerting
# ==============================================================================
# Comprehensive observability for tracking sync pipeline SLA (<30 mins),
# operational failures, poison messages in DLQ, and product rejection rates.
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. SNS Alert Topic
# ------------------------------------------------------------------------------
resource "aws_sns_topic" "pipeline_alerts" {
  name = "${local.name_prefix}-alerts"

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-alerts"
    Description = "SNS topic notifying on pipeline failures and high error rates"
  })
}

resource "aws_sns_topic_subscription" "email_alert" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.pipeline_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ------------------------------------------------------------------------------
# 2. CloudWatch Log Groups for Lambdas and Step Functions
# ------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "lambda_chunk_and_enqueue" {
  name              = "/aws/lambda/${local.name_prefix}-chunk-and-enqueue"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "lambda_sync_batch" {
  name              = "/aws/lambda/${local.name_prefix}-sync-batch"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "lambda_check_sync_status" {
  name              = "/aws/lambda/${local.name_prefix}-check-sync-status"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "lambda_finalize_run" {
  name              = "/aws/lambda/${local.name_prefix}-finalize-run"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/aws/vendedlogs/states/${local.name_prefix}-pipeline"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

# ------------------------------------------------------------------------------
# 3. Metric Filter: High Rejection Rate Extraction
# ------------------------------------------------------------------------------
# Extracts counts of rejected product items reported by the sync-batch worker.
resource "aws_cloudwatch_log_metric_filter" "wms_rejected_items" {
  name           = "${local.name_prefix}-wms-rejected-items"
  log_group_name = aws_cloudwatch_log_group.lambda_sync_batch.name
  pattern        = "{ $.status = \"REJECTED\" || $.level = \"warning\" && $.event = \"*rejected*\" }"

  metric_transformation {
    name          = "RejectedItemsCount"
    namespace     = "CatalogueSync/${var.environment}"
    value         = "1"
    default_value = 0
  }
}

# ------------------------------------------------------------------------------
# 4. Metric Alarms
# ------------------------------------------------------------------------------

# Alarm 1: Step Functions Pipeline Run Failure
resource "aws_cloudwatch_metric_alarm" "pipeline_failure" {
  alarm_name          = "${local.name_prefix}-pipeline-execution-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Triggers immediately if the Step Functions catalogue sync pipeline fails."
  alarm_actions       = [aws_sns_topic.pipeline_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline.arn
  }

  tags = local.common_tags
}

# Alarm 2: DLQ Depth Alarm (>0 messages visible in dead letter queue)
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${local.name_prefix}-sqs-dlq-messages-detected"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Triggers when poison batches are routed to DLQ after 3 failed attempts."
  alarm_actions       = [aws_sns_topic.pipeline_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.wms_sync_batches_dlq.name
  }

  tags = local.common_tags
}

# Alarm 3: High Rejection Rate Alarm
resource "aws_cloudwatch_metric_alarm" "high_rejection_rate" {
  alarm_name          = "${local.name_prefix}-high-product-rejection-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RejectedItemsCount"
  namespace           = "CatalogueSync/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = 50
  alarm_description   = "Triggers when more than 50 product records are rejected by WMS in a 5-minute window."
  alarm_actions       = [aws_sns_topic.pipeline_alerts.arn]
  treat_missing_data  = "notBreaching"

  tags = local.common_tags
}

# Alarm 4: Lambda Batch Worker Invocation Errors
resource "aws_cloudwatch_metric_alarm" "sync_batch_errors" {
  alarm_name          = "${local.name_prefix}-sync-batch-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Triggers when sync-batch worker experiences consecutive Lambda execution errors."
  alarm_actions       = [aws_sns_topic.pipeline_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.sync_batch.function_name
  }

  tags = local.common_tags
}
