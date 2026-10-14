# ==============================================================================
# IAM Roles and Least-Privilege Access Policies
# ==============================================================================
# Granular IAM roles created per component to strictly enforce least privilege.
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. ECS Task Execution Role (Used by ECS agent to pull images & push logs)
# ------------------------------------------------------------------------------
resource "aws_iam_role" "ecs_execution_role" {
  name        = "${local.name_prefix}-ecs-execution-role"
  description = "Role assumed by ECS agent to pull container images and push logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "ecs_execution_policy" {
  name = "${local.name_prefix}-ecs-execution-policy"
  role = aws_iam_role.ecs_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRImagePull"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.ecs_export.arn}:*"
      },
      {
        Sid    = "SecretsManagerAccess"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.product_api_key.arn
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 2. ECS Task Role (Used by the container application at runtime)
# ------------------------------------------------------------------------------
resource "aws_iam_role" "ecs_task_role" {
  name        = "${local.name_prefix}-ecs-task-role"
  description = "Runtime permissions for the catalogue export ECS container"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "ecs_task_policy" {
  name = "${local.name_prefix}-ecs-task-policy"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3CatalogueExportWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:AbortMultipartUpload"
        ]
        Resource = "${aws_s3_bucket.catalogue_exports.arn}/*"
      },
      {
        Sid    = "DynamoDBSyncRunsAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = aws_dynamodb_table.sync_runs.arn
      },
      {
        Sid    = "SecretsManagerRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = aws_secretsmanager_secret.product_api_key.arn
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 3. Step Functions Execution Role
# ------------------------------------------------------------------------------
resource "aws_iam_role" "step_functions_role" {
  name        = "${local.name_prefix}-sfn-execution-role"
  description = "Role assumed by AWS Step Functions to orchestrate ECS and Lambdas"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "states.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "step_functions_policy" {
  name = "${local.name_prefix}-sfn-execution-policy"
  role = aws_iam_role.step_functions_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECSRunTask"
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks"
        ]
        Resource = "*"
      },
      {
        Sid    = "PassRoleToECS"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          aws_iam_role.ecs_execution_role.arn,
          aws_iam_role.ecs_task_role.arn
        ]
      },
      {
        Sid    = "LambdaInvoke"
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = [
          aws_lambda_function.chunk_and_enqueue.arn,
          "${aws_lambda_function.chunk_and_enqueue.arn}:*",
          aws_lambda_function.sync_batch.arn,
          "${aws_lambda_function.sync_batch.arn}:*",
          aws_lambda_function.check_sync_status.arn,
          "${aws_lambda_function.check_sync_status.arn}:*",
          aws_lambda_function.finalize_run.arn,
          "${aws_lambda_function.finalize_run.arn}:*"
        ]
      },
      {
        Sid    = "CloudWatchLogsDelivery"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      {
        Sid    = "EventBridgeIntegration"
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule"
        ]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule*"
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 4. Lambda: Chunk and Enqueue Role
# ------------------------------------------------------------------------------
resource "aws_iam_role" "chunk_and_enqueue_role" {
  name        = "${local.name_prefix}-chunk-enqueue-role"
  description = "Role for Chunk and Enqueue Lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "chunk_and_enqueue_policy" {
  name = "${local.name_prefix}-chunk-enqueue-policy"
  role = aws_iam_role.chunk_and_enqueue_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BasicLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_chunk_and_enqueue.arn}:*"
      },
      {
        Sid    = "S3ReadExports"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.catalogue_exports.arn}/*"
      },
      {
        Sid    = "SQSSendBatches"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:SendMessageBatch"
        ]
        Resource = aws_sqs_queue.wms_sync_batches.arn
      },
      {
        Sid    = "DynamoDBRunStateUpdate"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem"
        ]
        Resource = aws_dynamodb_table.sync_runs.arn
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 5. Lambda: Sync Batch Worker Role
# ------------------------------------------------------------------------------
resource "aws_iam_role" "sync_batch_role" {
  name        = "${local.name_prefix}-sync-batch-role"
  description = "Role for Sync Batch Lambda worker"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "sync_batch_policy" {
  name = "${local.name_prefix}-sync-batch-policy"
  role = aws_iam_role.sync_batch_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BasicLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_sync_batch.arn}:*"
      },
      {
        Sid    = "SQSConsumeBatches"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.wms_sync_batches.arn
      },
      {
        Sid    = "DynamoDBItemTracking"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:BatchWriteItem",
          "dynamodb:BatchGetItem"
        ]
        Resource = aws_dynamodb_table.sync_items.arn
      },
      {
        Sid    = "DynamoDBRunMetricsUpdate"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem"
        ]
        Resource = aws_dynamodb_table.sync_runs.arn
      },
      {
        Sid    = "SecretsManagerReadWMSKey"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = aws_secretsmanager_secret.warehouse_api_key.arn
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 6. Lambda: Check Sync Status Role
# ------------------------------------------------------------------------------
resource "aws_iam_role" "check_sync_status_role" {
  name        = "${local.name_prefix}-check-status-role"
  description = "Role for Check Sync Status polling Lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "check_sync_status_policy" {
  name = "${local.name_prefix}-check-status-policy"
  role = aws_iam_role.check_sync_status_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BasicLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_check_sync_status.arn}:*"
      },
      {
        Sid    = "SQSCheckQueueDepth"
        Effect = "Allow"
        Action = [
          "sqs:GetQueueAttributes"
        ]
        Resource = [
          aws_sqs_queue.wms_sync_batches.arn,
          aws_sqs_queue.wms_sync_batches_dlq.arn
        ]
      },
      {
        Sid    = "DynamoDBReadRunState"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.sync_runs.arn,
          aws_dynamodb_table.sync_items.arn
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------------------
# 7. Lambda: Finalize Run Role
# ------------------------------------------------------------------------------
resource "aws_iam_role" "finalize_run_role" {
  name        = "${local.name_prefix}-finalize-run-role"
  description = "Role for Finalize Run Lambda aggregator"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "finalize_run_policy" {
  name = "${local.name_prefix}-finalize-run-policy"
  role = aws_iam_role.finalize_run_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BasicLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_finalize_run.arn}:*"
      },
      {
        Sid    = "DynamoDBFinalizeRunsAndItems"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.sync_runs.arn,
          aws_dynamodb_table.sync_items.arn,
          "${aws_dynamodb_table.sync_items.arn}/index/*"
        ]
      },
      {
        Sid    = "SNSPublishAlerts"
        Effect = "Allow"
        Action = [
          "sns:Publish"
        ]
        Resource = aws_sns_topic.pipeline_alerts.arn
      }
    ]
  })
}
