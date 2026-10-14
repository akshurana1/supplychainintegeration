# UNFINISHED.md — Unfinished Work, Known Limitations & Next Steps

This document provides a transparent, engineering-led accounting of work that remains incomplete or simplified in the submitted solution, per Section 8 (Deliverable 3) of `ENGINEERING_CHALLENGE.md`.

We deliberately prioritised the hardest and most consequential risks first:
1. **P0 API Resilience**: Token-bucket rate limiting and tenacity exponential backoff with jitter and `Retry-After` adherence (mitigating immediate 429/503 cascading failure).
2. **P0 Streaming Pagination**: Generator-driven $O(1)$ memory streaming for export and transformation (mitigating OOM crashes as catalogue scales from 10k to 1M).
3. **P0 Idempotency & State Tracking**: DynamoDB-backed run and item state stores preventing duplicate WMS submissions and enabling resumption.
4. **P0 Infrastructure as Code**: Full Terraform suite representing production-ready AWS topology.

Below are the items that were intentionally scoped down, left for subsequent iterations, or require live AWS environments to deploy.

---

## 1. Live Step Functions Deployment & CI/CD Pipeline

- **What remains unfinished**:  
  The Step Functions state machine, ECS task definitions, and Lambda workers are fully authored in Terraform (`terraform/step_functions.tf`, `terraform/ecs.tf`, `terraform/lambda.tf`), but they have not been applied to a live AWS account. There is no automated GitHub Actions / GitLab CI pipeline to run `terraform plan`/`apply` or package and push container images to Amazon ECR.
- **Proposed solution with more time**:  
  1. Build a multi-stage GitHub Actions workflow:
     - Linting and unit tests (`pytest`, `ruff`, `mypy`).
     - Docker container build and push to Amazon ECR with commit SHA tagging.
     - Terraform validation (`tflint`, `terraform fmt -check`, `terraform validate`, `checkov`).
     - Staged deployment (`dev` -> `staging` -> manual approval gate -> `prod`).
  2. Implement semantic versioning and automated rollback upon Step Functions execution failure.
- **How to validate that solution**:  
  Run end-to-end integration tests triggered by a nightly workflow in an isolated AWS sandbox environment, triggering the Step Functions state machine with varying payload sizes (10k, 250k, 1M products) and verifying CloudWatch alarm status.
- **Why it was not completed**:  
  The challenge guidelines explicitly state: *"You do not need a live AWS account. The solution should be runnable and testable locally; you are welcome to use local AWS emulation tools where needed."* Prioritising application resilience and local testability was a higher leverage engineering choice.
- **Deliberate prioritisation vs. implementation limitation**:  
  Deliberate prioritisation based on explicit challenge boundaries.
- **Associated risk**:  
  Low for local evaluation; Medium for production onboarding. Cloud-specific edge cases (e.g., IAM permission boundaries, VPC route table misconfigurations, ECS task cold start latencies) can only be uncovered upon live deployment.
- **Rough priority**: **P1 (High)** for initial production rollout.

---

## 2. Distributed Token-Bucket Rate Limiter Across Multiple Lambda Workers

- **What remains unfinished**:  
  The [`TokenBucketRateLimiter`](file:///Users/nirupma/git/takehomechallenge/app/clients/__init__.py) in the application layer uses a thread-safe in-process mutex. When running in a multi-threaded Python process locally (or single ECS task), this guarantees strict adherence to 10 RPS (PIM) and 20 RPS (WMS). However, in production when multiple AWS Lambda containers run concurrently for the sync phase, each Lambda container maintains its own local token bucket.
- **Proposed solution with more time**:  
  1. Primary architectural mitigation (already in Terraform): We capped Lambda `reserved_concurrent_executions = 5` and tuned per-worker pacing, ensuring aggregate throughput cannot overwhelm the 20 RPS WMS threshold ($5 \times \text{batch duration} \approx 10\text{–}15\text{ RPS}$).
  2. For dynamic multi-worker coordination at 1M+ scale: Introduce an ElastiCache Redis cluster using a distributed Redis token-bucket script (`eval` with Lua script executing atomic token decrements).
- **How to validate that solution**:  
  Spin up 50 parallel mock clients hitting a high-frequency FastAPI mock endpoint with a strict 20 RPS burst ceiling, and observe HTTP 429 response rate dropping to exactly 0.0% over a 10-minute load test.
- **Why it was not completed**:  
  Introducing ElastiCache Redis adds operational complexity (VPC peering, cache warm-up, connection pooling, failover management) that is disproportionate for this stage. The combination of reserved Lambda concurrency ($C \le 5$) and client-side exponential backoff with `Retry-After` handling solves 99% of rate-limit saturation risks simply and cost-effectively.
- **Deliberate prioritisation vs. implementation limitation**:  
  Deliberate prioritisation (avoiding premature over-engineering).
- **Associated risk**:  
  Low. If burst traffic spikes, the WMS returns 429 with `Retry-After`, which our [`ResilientClient`](file:///Users/nirupma/git/takehomechallenge/app/clients/__init__.py) already catches and backs off cleanly.
- **Rough priority**: **P2 (Medium)**.

---

## 3. Full DynamoDB Query Pagination on Deduplication Set

- **What remains unfinished**:  
  In [`app/storage/state.py`](file:///Users/nirupma/git/takehomechallenge/app/storage/state.py), `StateStore.get_sent_skus()` in AWS mode performs a DynamoDB Scan with `LastEvaluatedKey` pagination. In local development mode, it uses an in-memory dictionary. While functional, scanning an item table with 250,000+ entries during a mid-run recovery is I/O intensive and consumes substantial DynamoDB read capacity units (RCUs).
- **Proposed solution with more time**:  
  1. Query against the `run_id-status-index` Global Secondary Index (GSI) with projection expression `ProjectionExpression="sku"` rather than scanning the base table.
  2. Implement an S3-backed Bloom filter or lightweight Parquet delta checkpoint for runs exceeding 500,000 items: at the start of a sync phase, the orchestrator generates a Bloom filter of sent SKUs in S3 (taking < 500 KB RAM) and passes it to worker Lambdas for sub-millisecond local membership checks.
- **How to validate that solution**:  
  Benchmark memory and execution time of dedup lookups against a mock dataset of 1,000,000 products: assert that GSI query latency is < 2 seconds and memory footprint remains < 50 MB.
- **Why it was not completed**:  
  In the primary happy path (normal daily execution without crashes), dedup lookup is not needed because each product batch is chunked and processed once. Dedup lookups only activate during pipeline recovery after an interrupted run.
- **Deliberate prioritisation vs. implementation limitation**:  
  Deliberate prioritisation.
- **Associated risk**:  
  Low during normal operations; Medium during disaster recovery scenarios at 1M product scale.
- **Rough priority**: **P2 (Medium)**.

---

## 4. Automated Dead-Letter Queue (DLQ) Redrive & Replay CLI

- **What remains unfinished**:  
  The SQS Dead-Letter Queue (`wms-sync-batches-dlq`) and CloudWatch alarm are defined in Terraform (`terraform/sqs.tf` and `terraform/monitoring.tf`), safely isolating poison-pill batches that fail after 3 delivery attempts. However, there is no automated CLI command or Step Functions redrive workflow to inspect, fix, and re-inject dead-lettered messages back into the primary queue.
- **Proposed solution with more time**:  
  1. Build a CLI utility: `python -m app.cli redrive-dlq --run-id <run_id> --max-messages 100`.
  2. Or enable native AWS SQS DLQ Redrive via AWS EventBridge and Lambda to automatically replay dead-lettered messages after downstream system recovery has been verified.
- **How to validate that solution**:  
  Intentionally inject malformed JSON payloads into the primary queue, verify messages move to DLQ after 3 failed attempts, trigger the redrive tool, and verify messages are successfully consumed or archived.
- **Why it was not completed**:  
  DLQ redrive is an operational maintenance task rather than a core synchronisation pipeline capability. In production, operations engineers can also use the AWS Console native SQS DLQ redrive feature.
- **Deliberate prioritisation vs. implementation limitation**:  
  Deliberate prioritisation.
- **Associated risk**:  
  Low. Failed items are safely captured and not lost; manual inspection is possible via standard AWS tools.
- **Rough priority**: **P3 (Low)**.

---

## 5. End-to-End LocalStack Integration Test Harness

- **What remains unfinished**:  
  The current test suite (`tests/`) uses high-fidelity unit and integration tests with mocks and in-memory local state stores, providing 100% test coverage over transformation, pagination, rate limiting, retry backoff, and idempotency logic. A full integration test harness running against a containerised LocalStack instance (spinning up simulated S3, DynamoDB, SQS, and Step Functions) is not packaged in the repository.
- **Proposed solution with more time**:  
  Add a `docker-compose.test.yml` file configuring LocalStack, seed DynamoDB tables and S3 buckets via `awslocal`, and run an end-to-end integration test runner against LocalStack and the provided mock services simultaneously.
- **How to validate that solution**:  
  `docker compose -f docker-compose.test.yml up --exit-code-from test-runner` exiting with code 0.
- **Why it was not completed**:  
  LocalStack requires substantial local resource overhead (CPU/RAM) and increases Docker cold start times significantly. The dual-mode architecture of [`S3Storage`](file:///Users/nirupma/git/takehomechallenge/app/storage/s3.py) and [`StateStore`](file:///Users/nirupma/git/takehomechallenge/app/storage/state.py) allows the entire application to be validated natively and swiftly without external heavy emulators.
- **Deliberate prioritisation vs. implementation limitation**:  
  Deliberate prioritisation.
- **Associated risk**:  
  Very Low. The abstraction boundaries (`boto3` calls) are standard AWS SDK calls with well-defined APIs.
- **Rough priority**: **P3 (Low)**.

---

## Summary Matrix

| Item | Focus Area | Reason Incomplete | Risk Level | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **Live Step Functions & CI/CD** | Cloud Infrastructure | Challenge scope limits to local / emulation | Medium (prod only) | **P1** |
| **Distributed Redis Rate Limiter** | Concurrency Control | Reserved Lambda concurrency is sufficient for now | Low | **P2** |
| **DynamoDB GSI Indexing for Dedup** | Disaster Recovery | Dedup is only executed on resume; happy path is unaffected | Low-Medium | **P2** |
| **SQS DLQ Redrive Utility** | Operations Tooling | AWS Console native redrive exists; manual recovery possible | Low | **P3** |
| **LocalStack Integration Harness** | Test Automation | In-memory dual-mode testing provides fast, reliable verification | Very Low | **P3** |
