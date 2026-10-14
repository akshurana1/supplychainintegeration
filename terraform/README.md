# Catalogue Sync Platform — Terraform Infrastructure

This directory contains the production-grade Terraform configuration for the PIM-to-WMS catalogue synchronisation pipeline, designed to reliably handle from 250,000 to over 1,000,000 product records within a 30-minute SLA window.

## Architecture Highlights

```
EventBridge / API Trigger
         │
         ▼
AWS Step Functions State Machine
         │
         ├──► 1. ExportCatalogue (ECS Fargate Task)
         │       └─ Paginates PIM API, streams CSV directly into Amazon S3
         │
         ├──► 2. ChunkAndEnqueue (AWS Lambda)
         │       └─ Streams S3 CSV, slices items into batches of 100, enqueues to SQS
         │
         ├──► 3. WaitForSync Loop (AWS Lambda + SQS + DynamoDB)
         │       ├─ SQS Queue (`wms-sync-batches`) buffers batches
         │       ├─ Worker Lambda (`sync-batch`) processes with concurrency = 5
         │       ├─ Deduplication & idempotency enforced via DynamoDB (`sync_items`)
         │       └─ Status Lambda (`check-sync-status`) polls SQS queue depth
         │
         └──► 4. Finalize (AWS Lambda)
                 └─ Aggregates run outcomes, updates `sync_runs`, publishes metrics/alerts
```

## Module & File Structure

| File | Component / Resource | Purpose |
|------|----------------------|---------|
| `main.tf` | Provider Configuration | AWS provider config (`ap-south-1`), default tags, contextual data sources |
| `variables.tf` | Input Variables | Parameterises regions, environments, memory, timeouts, concurrency, retention |
| `s3.tf` | Amazon S3 | Versioned, SSE-S3 encrypted bucket with 90-day object lifecycle expiration |
| `dynamodb.tf` | Amazon DynamoDB | `sync_runs` (run lifecycle, PITR) and `sync_items` (idempotency, GSI, TTL) |
| `sqs.tf` | Amazon SQS | Batch worker queue (`wms-sync-batches`, 300s visibility) + DLQ (`maxReceiveCount = 3`) |
| `step_functions.tf` | AWS Step Functions | 4-phase state machine orchestrator with retries, catch blocks, and CW logging |
| `ecs.tf` | ECS on AWS Fargate | Serverless container task for PIM pagination avoiding Lambda 15m limits |
| `lambda.tf` | AWS Lambda | 4 Python 3.11 functions + SQS Event Source Mapping with reserved concurrency |
| `iam.tf` | AWS IAM | Granular least-privilege execution roles and policies for all components |
| `monitoring.tf` | CloudWatch & SNS | Component log groups (30d retention), metric filter, DLQ/failure alarms, SNS topic |
| `secrets.tf` | AWS Secrets Manager | Encrypted storage for PIM and WMS API authentication credentials |
| `outputs.tf` | Outputs | State machine ARN, S3 bucket, DynamoDB tables, SQS URLs, Lambda ARNs |

## Design Decisions & Trade-Offs

1. **ECS Fargate for PIM Export vs. Lambda**:
   At 1M records and 10 RPS with 500 items/page, exporting requires 2,000 pages (~200–400s under normal latency, extending up to 10–15m during transient throttles). ECS Fargate eliminates Lambda's hard 15-minute execution barrier while remaining fully serverless with zero idle cost.

2. **SQS Buffering & Lambda Concurrency Limit**:
   The WMS API enforces a strict rate limit of 20 RPS. To prevent downstream saturation and HTTP 429 cascades, `sync-batch` uses `reserved_concurrent_executions = var.sync_concurrency` (default 5 workers, max 20). SQS absorbs spikes from fast CSV chunking.

3. **DynamoDB Idempotency & TTL**:
   The composite key `run_sku` (`run_id#sku`) on `sync_items` allows O(1) single-item lookups to verify whether an item was already submitted within a run before dispatching ("never send twice"). A GSI on `run_id` facilitates rapid end-of-run aggregation, and DynamoDB TTL automatically purges historical records without operational overhead.

4. **Fault Tolerance & Dead Lettering**:
   Any poison batch that fails 3 consecutive delivery attempts is segregated into `wms-sync-batches-dlq` (retained for 14 days), triggering a CloudWatch alarm to the operations team via SNS without terminating or stalling the rest of the 10,000 batches.

## Deployment Instructions

```bash
# Initialize Terraform and download providers
terraform init

# Validate syntax and configuration
terraform validate

# Review proposed changes
terraform plan -var-file="terraform.tfvars"

# Apply configuration
terraform apply -var-file="terraform.tfvars"
```
