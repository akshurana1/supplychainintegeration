# ==============================================================================
# AWS Step Functions State Machine Pipeline
# ==============================================================================
# Orchestrates the end-to-end catalogue synchronisation lifecycle:
#
#  [Start]
#     │
#     ▼
# [ExportCatalogue]  ── (ECS Fargate Task - paginates PIM -> creates CSV in S3)
#     │
#     ▼
# [ChunkAndEnqueue]  ── (Lambda - chunks CSV into batches -> pushes to SQS)
#     │
#     ▼
# [CheckSyncStatus] ◄──┐ (Lambda - inspects SQS queue depth & DynamoDB)
#     │                │
#     ▼                │
# [IsSyncComplete?] ── (Choice: false -> wait 30s -> loop back)
#     │ (true)
#     ▼
# [Finalize]         ── (Lambda - aggregates metrics & marks run COMPLETED)
#     │
#     ▼
# [RunSuccess]
#
# Resilience:
# - All states have automated exponential backoff retries for transient errors.
# - Catch blocks capture fatal errors and transition to FailRun for operational alerting.
# ==============================================================================

# Fetch default VPC and subnets for running the Fargate export task
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.name_prefix}-pipeline"
  role_arn = aws_iam_role.step_functions_role.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Catalogue Synchronisation Pipeline Orchestrator"
    StartAt = "ExportCatalogue"
    States = {
      # ------------------------------------------------------------------------
      # State 1: Export Catalogue (ECS Fargate Task)
      # ------------------------------------------------------------------------
      ExportCatalogue = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          LaunchType     = "FARGATE"
          Cluster        = aws_ecs_cluster.main.arn
          TaskDefinition = aws_ecs_task_definition.export_catalogue.arn
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = data.aws_subnets.default.ids
              AssignPublicIp = "ENABLED"
            }
          }
          Overrides = {
            ContainerOverrides = [
              {
                Name = "export-catalogue"
                Environment = [
                  {
                    "Name" : "RUN_ID",
                    "Value.$" : "$.run_id"
                  }
                ]
              }
            ]
          }
        }
        ResultPath = "$.ExportResult"
        Retry = [
          {
            ErrorEquals     = ["ECS.AmazonECSException", "States.TaskFailed"]
            IntervalSeconds = 30
            MaxAttempts     = 2
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "FailRun"
          }
        ]
        Next = "ChunkAndEnqueue"
      }

      # ------------------------------------------------------------------------
      # State 2: Chunk and Enqueue (Lambda)
      # ------------------------------------------------------------------------
      ChunkAndEnqueue = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName   = aws_lambda_function.chunk_and_enqueue.arn
          "Payload.$"    = "$"
        }
        ResultSelector = {
          "Payload.$" = "$.Payload"
        }
        ResultPath = "$.ChunkResult"
        Retry = [
          {
            ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException"]
            IntervalSeconds = 5
            MaxAttempts     = 3
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "FailRun"
          }
        ]
        Next = "CheckSyncStatus"
      }

      # ------------------------------------------------------------------------
      # State 3: WaitForSync Polling Loop (Lambda + Choice + Wait)
      # ------------------------------------------------------------------------
      CheckSyncStatus = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName   = aws_lambda_function.check_sync_status.arn
          "Payload.$"    = "$"
        }
        ResultSelector = {
          "is_complete.$"      = "$.Payload.is_complete"
          "status.$"           = "$.Payload.status"
          "pending_batches.$"  = "$.Payload.pending_batches"
        }
        ResultPath = "$.SyncStatus"
        Retry = [
          {
            ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException"]
            IntervalSeconds = 5
            MaxAttempts     = 3
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "FailRun"
          }
        ]
        Next = "IsSyncComplete"
      }

      IsSyncComplete = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.SyncStatus.is_complete"
            BooleanEquals = true
            Next          = "Finalize"
          }
        ]
        Default = "WaitForPolling"
      }

      WaitForPolling = {
        Type    = "Wait"
        Seconds = 30
        Next    = "CheckSyncStatus"
      }

      # ------------------------------------------------------------------------
      # State 4: Finalize Run (Lambda)
      # ------------------------------------------------------------------------
      Finalize = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName   = aws_lambda_function.finalize_run.arn
          "Payload.$"    = "$"
        }
        ResultPath = "$.FinalizeResult"
        Retry = [
          {
            ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException"]
            IntervalSeconds = 5
            MaxAttempts     = 3
            BackoffRate     = 2.0
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "FailRun"
          }
        ]
        Next = "RunSuccess"
      }

      # ------------------------------------------------------------------------
      # Terminal States
      # ------------------------------------------------------------------------
      RunSuccess = {
        Type = "Succeed"
      }

      FailRun = {
        Type  = "Fail"
        Cause = "Pipeline execution encountered an unrecoverable failure during processing."
        Error = "PipelineExecutionFailed"
      }
    }
  })

  tags = merge(local.common_tags, {
    Name        = "${local.name_prefix}-pipeline"
    Description = "Step Functions state machine for catalogue sync"
  })
}
