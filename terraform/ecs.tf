# ==============================================================================
# ECS Fargate Resources for Catalogue Export Phase
# ==============================================================================
# PIM pagination and CSV export run as a dedicated ECS Fargate task.
#
# Design Rationale:
# 1. Long-Running Workloads: At 1M products and 10 RPS (500 items/page = 2,000 pages),
#    exporting takes ~200-300 seconds minimum, with potential transient backoff.
#    Running on Fargate avoids Lambda's hard 15-minute execution limit.
# 2. Ephemeral Compute: Serverless Fargate tasks spin up on demand when triggered
#    by Step Functions, incurring zero compute cost when idle.
# 3. Secure Credential Injection: PIM API keys are securely retrieved from AWS Secrets
#    Manager via the ECS agent at task launch, never stored in plain text.
# ==============================================================================

# ------------------------------------------------------------------------------
# ECS Cluster
# ------------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-cluster"
    Description = "ECS cluster running batch catalogue export jobs"
  })
}

# ------------------------------------------------------------------------------
# CloudWatch Log Group for Export Task
# ------------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "ecs_export" {
  name              = "/ecs/${local.name_prefix}-export-catalogue"
  retention_in_days = var.log_retention_days

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-ecs-export-logs"
    Description = "Log group for ECS catalogue export task"
  })
}

# ------------------------------------------------------------------------------
# ECS Fargate Task Definition
# ------------------------------------------------------------------------------
resource "aws_ecs_task_definition" "export_catalogue" {
  family                   = "${local.name_prefix}-export-catalogue"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.ecs_cpu)
  memory                   = tostring(var.ecs_memory)
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "export-catalogue"
      image     = var.ecs_image
      essential = true

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs_export.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      environment = [
        {
          name  = "ENVIRONMENT"
          value = var.environment
        },
        {
          name  = "AWS_REGION"
          value = var.aws_region
        },
        {
          name  = "PRODUCT_API_URL"
          value = var.product_api_url
        },
        {
          name  = "S3_BUCKET"
          value = aws_s3_bucket.catalogue_exports.bucket
        },
        {
          name  = "DYNAMODB_TABLE_RUNS"
          value = aws_dynamodb_table.sync_runs.name
        }
      ]

      secrets = [
        {
          name      = "PRODUCT_API_KEY"
          valueFrom = aws_secretsmanager_secret.product_api_key.arn
        }
      ]
    }
  ])

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-export-catalogue-task"
    Description = "Task definition for PIM catalogue export"
  })
}
