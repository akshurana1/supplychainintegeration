# Product Catalogue Integration Platform (PIM → WMS)

Production-ready, highly resilient, and scalable supply chain integration platform synchronising product catalogues from a central **Product Information Management (PIM)** system into a **Warehouse Management System (WMS)** on AWS.

Designed and engineered to scale from 10,000 to **1,000,000 products** while strictly adhering to a **30-minute daily SLA**, respecting API rate limits, providing at-most-once delivery guarantees ("same product must never be sent twice"), and enabling mid-flight recovery.

---

## Deliverables Summary

| Deliverable | Location | Description |
| :--- | :--- | :--- |
| **Architecture & Justification** | [`ARCHITECTURE.md`](ARCHITECTURE.md) | Comprehensive architecture document, Mermaid diagrams, performance model for 1M products, failure & retry flows, rate-limit strategies, AWS trade-offs, and requirement assumptions. |
| **Production Code** | [`app/`](app/) | Resilient HTTP clients, streaming pagination, S3 storage abstraction, DynamoDB state tracking, concurrent batching, and orchestrator. |
| **Automated Tests** | [`tests/`](tests/) | 44 unit and integration tests covering exponential backoff, rate limiters, pagination, deduplication, transform, and orchestrator resume. |
| **Terraform Infrastructure** | [`terraform/`](terraform/) | Complete AWS infrastructure-as-code: Step Functions state machine, ECS Fargate, Lambda workers, S3 (90-day retention), DynamoDB tables, SQS + DLQ, CloudWatch alarms, Secrets Manager, and IAM least-privilege roles. |
| **Unfinished Work & Next Steps** | [`UNFINISHED.md`](UNFINISHED.md) | Transparent accounting of deferred items, risk evaluations, and priority next steps. |

---

## Key Engineering Improvements

1. **Resilient HTTP Client ([`app/clients/__init__.py`](app/clients/__init__.py))**:
   - Replaced naive single-retry sleep with `tenacity` exponential backoff, random jitter, and dynamic `Retry-After` header adherence.
   - Integrated token-bucket rate limiters enforcing PIM (10 RPS) and WMS (20 RPS) documented boundaries.
2. **Streaming $O(1)$ Memory Pagination ([`app/clients/product_api.py`](app/clients/product_api.py) & [`app/services/catalogue_export.py`](app/services/catalogue_export.py))**:
   - Generator-based pagination streams records directly to CSV and S3 row-by-row, eliminating $O(N)$ RAM exhaustion at 1,000,000 products.
   - Checkpoints `last_export_page` to enable zero-loss resume on failure.
3. **Idempotency & Deduplication ("Never Send Twice") ([`app/storage/state.py`](app/storage/state.py))**:
   - State-tracked item-level records (`run_sku`) record items as `SENT` before invoking the WMS API, guaranteeing at-most-once delivery within a run.
   - On pipeline re-runs or crashes, already-sent SKUs are automatically skipped.
4. **Fault-Isolated Concurrent Batching ([`app/services/warehouse_sync.py`](app/services/warehouse_sync.py))**:
   - Sends 100-item batches concurrently using `ThreadPoolExecutor` (local) or AWS Lambda + SQS (cloud), isolated so a failing batch does not abort valid batches.
   - Granular parsing of mixed `accepted` and `rejected` items from the WMS.
5. **Structured Observability ([`app/observability/__init__.py`](app/observability/__init__.py))**:
   - JSON-formatted logs with correlation context (`run_id`) across all components.

---

## Performance Model (1M Scale vs 30-Minute SLA)

| Pipeline Phase | Calculation at 1,000,000 Products | Estimated Duration |
| :--- | :--- | :--- |
| **Export Phase** | 2,000 pages (500 items/page) @ 10 RPS | **~3.3 minutes** |
| **Chunking Phase** | S3 streaming CSV read into 10,000 SQS batch messages | **~0.5 minutes** |
| **Sync Phase** | 10,000 batches (100 items/batch) @ 20 RPS sustained | **~8.3 minutes** |
| **Total Runtime** | **Complete end-to-end catalogue run** | **~12.1 minutes** |

> **Result**: The pipeline completes in **~12 minutes**, well inside the **30-minute business window**, providing a 60% safety buffer for network variability and retries.

---

## Local Development & Testing

### Quick Start (One Command)
You can set up dependencies, launch mock services, and run the pipeline using `start.sh`:

```bash
# Make executable (if needed)
chmod +x start.sh

# 1. Run all 45 automated tests
./start.sh --test

# 2. Start mock services in background (Docker or native Python fallback)
./start.sh --mocks-only

# 3. Run full sync pipeline (preserves run history in state store)
./start.sh

# 4. View all sync runs and their statuses
./start.sh --list

# 5. Resume an interrupted run
./start.sh --resume <RUN_ID>

# 6. Clean up previous migration and database state
./start.sh --clean

# 7. Stop running mock services
./start.sh --stop
```

### Manual Setup & Execution
If you prefer running commands manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Test Suite
The automated test suite runs completely offline with no cloud dependencies:
```bash
pytest -v
```

### 3. Run Mock Services (PIM & WMS APIs)
In a separate terminal, launch the mock APIs using Docker Compose:
```bash
docker compose up --build
```
- **Product API (PIM)**: `http://localhost:8001` (Swagger docs: `http://localhost:8001/docs`)
- **Warehouse API (WMS)**: `http://localhost:8002` (Swagger docs: `http://localhost:8002/docs`)

### 4. Execute the Synchronisation Pipeline
Run a fresh daily synchronisation run:
```bash
python -m app.main
```

### 5. Resuming an Interrupted Run
If a run is interrupted or fails midway, resume it using its run ID:
```bash
python -m app.main --resume <RUN_ID>
```
The orchestrator inspects the state store, skips completed phases, resumes export from the last fetched page, and uses deduplication tracking to skip already-sent products.

---

## Configuration

All configuration is managed via [`app/config.py`](app/config.py) using `pydantic-settings` and can be overridden via environment variables:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PRODUCT_API_URL` | `http://localhost:8001` | Base URL for PIM API |
| `PRODUCT_API_KEY` | `challenge-product-key` | API Key for PIM authentication |
| `PRODUCT_API_MAX_RPS` | `10.0` | Maximum requests per second to PIM |
| `PAGE_SIZE` | `500` | PIM page size (1–500) |
| `WAREHOUSE_API_URL` | `http://localhost:8002` | Base URL for WMS API |
| `WAREHOUSE_API_KEY` | `challenge-warehouse-key` | API Key for WMS authentication |
| `WAREHOUSE_API_MAX_RPS` | `20.0` | Maximum sustained requests per second to WMS |
| `BATCH_SIZE` | `100` | WMS batch size (max 100) |
| `USE_LOCAL_STORAGE` | `True` | Set `False` for AWS S3 and DynamoDB |
| `SYNC_CONCURRENCY` | `5` | Concurrency for WMS batch workers |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Terraform Infrastructure

The complete AWS architecture is defined in [`terraform/`](terraform/):
- **`terraform/main.tf`**: AWS Provider configuration for `ap-south-1`.
- **`terraform/step_functions.tf`**: AWS Step Functions state machine orchestrating export, chunking, sync, and finalization.
- **`terraform/ecs.tf`**: ECS Fargate task definition for long-running export.
- **`terraform/lambda.tf`**: Worker Lambdas with reserved concurrency protection.
- **`terraform/s3.tf`**: S3 bucket with 90-day expiration lifecycle policy.
- **`terraform/dynamodb.tf`**: `sync_runs` and `sync_items` tables with PITR and TTL.
- **`terraform/sqs.tf`**: SQS queue and Dead Letter Queue (`wms-sync-batches-dlq`).
- **`terraform/monitoring.tf`**: CloudWatch Log Groups, custom metric filters, alarms, and SNS alerting.
- **`terraform/secrets.tf`**: AWS Secrets Manager secrets for API credentials.
- **`terraform/iam.tf`**: Least-privilege IAM roles and execution policies.

To plan or validate the Terraform configuration:
```bash
cd terraform
terraform init
terraform validate
```
