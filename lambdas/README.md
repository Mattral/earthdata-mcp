# AWS Lambda Functions - Earthdata MCP

This directory contains AWS Lambda functions that power the Earthdata MCP semantic search pipeline. All lambdas follow NASA Enterprise security standards, AWS Well-Architected best practices, and support production-grade observability.

## Architecture Overview

```
CMR SNS Topics
    ↓
[Ingest Lambda] → FIFO SQS Queue → [Embedding Lambda] → PostgreSQL + pgvector
[Bootstrap Lambda] ↘ (bulk loads)  ↗
[Migration Lambda] → Database Schema Management
```

## Lambda Functions

### 1. Ingest Lambda (`ingest/handler.py`)
**Purpose:** Receive concept update/delete events from CMR SNS topic and forward to processing queue.

**Triggers:** CMR SNS topic notifications
**Queue Target:** FIFO SQS (concept-type:concept-id grouping)
**Concurrency:** Synchronous to SNS (invoked per notification batch)

**Key Features:**
- Message deduplication using concept-id:revision-id
- Ordered processing via FIFO grouping
- Input validation with Pydantic models
- Structured error logging with message ID tracking

**Configuration:**
```bash
EMBEDDING_QUEUE_URL=<sqs-queue-url-fifo>
```

**Deployment:**
```bash
# Package locally
pip install -r ingest/requirements.txt -t ingest/package/
cd ingest/package && zip -r ../ingest.zip . && cd ../.. && zip -j ingest/ingest.zip ingest/handler.py

# Deploy via Terraform
cd terraform/application && terraform apply -target=aws_lambda_function.ingest_lambda
```

**Monitoring:**
- **CloudWatch:** Check `/aws/lambda/<function-name>` logs
- **Metrics:** Monitor `Errors`, `Duration`, `Throttles` via CloudWatch Alarms
- **SQS:** Track `ApproximateNumberOfMessagesVisible` for queue depth

---

### 2. Embedding Lambda (`embedding/handler.py`)
**Purpose:** Generate embeddings for CMR concepts and store with vector search capability.

**Triggers:** FIFO SQS messages (ordered by concept-type:concept-id)
**Storage:** RDS PostgreSQL with pgvector extension
**Dependencies:** Bedrock (embeddings), CMR API, KMS lookup service

**Key Features:**
- Concept update handling: fetch metadata → extract chunks → generate embeddings → store
- Concept delete handling: remove embeddings and associations
- KMS term processing: shared embeddings across concepts with deduplication
- Langfuse integration for full operation tracing
- Partial batch response for reliable SQS processing

**Configuration:**
```bash
EMBEDDING_QUEUE_URL=<sqs-queue-url-fifo>
DATABASE_HOST=<rds-endpoint>
DATABASE_PORT=5432
DATABASE_NAME=embeddings
DATABASE_USER=postgres
DATABASE_PASSWORD=<secret>  # Via Secrets Manager
CMR_URL=https://cmr.earthdata.nasa.gov
EMBEDDING_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0  # Bedrock model ID
REDIS_URL=<redis-endpoint>:6379
LANGFUSE_PUBLIC_KEY=<key>
LANGFUSE_SECRET_KEY=<key>
```

**Deployment:**
```bash
# Docker build (included in terraform)
docker build -f ../../IngestLambdaDockerfile .

# Via Terraform (recommended)
cd terraform/application && terraform apply -target=aws_lambda_function.embedding_lambda
```

**Monitoring:**
- **Duration:** Generation and storage latency per concept
- **Embedding Errors:** EmbeddingError exceptions indicate Bedrock issues
- **CMR Errors:** CMRError exceptions indicate metadata fetch failures
- **Langfuse:** Real-time tracing of all embedding operations
- **CloudWatch Insights:**
  ```
  fields @timestamp, @message, concept_id, error_type
  | stats count(*) as errors by error_type
  ```

**Troubleshooting:**
| Error | Cause | Solution |
|-------|-------|----------|
| `CMRError: 404 Not Found` | Concept deleted in CMR | Handled gracefully, check audit logs |
| `EmbeddingError: ThrottlingException` | Bedrock rate limited | Increase Lambda concurrency or add exponential backoff |
| `ValidationError: ...` | Invalid JSON in SQS message | Check message format in ingest lambda logs |
| `psycopg.IntegrityError: duplicate key` | Race condition with other Lambda | Idempotency via upsert operations |

---

### 3. Bootstrap Lambda (`bootstrap/handler.py`)
**Purpose:** Bulk load CMR concepts into the embedding pipeline.

**Triggers:** Manual invocation via AWS Console or API
**Invocation Pattern:** Asynchronous (sends to SQS, returns immediately)

**Key Features:**
- Flexible CMR search with arbitrary search parameters
- Configurable page size (1-2000, default 500)
- Dry-run mode for validation without SQS writes
- Session tracking for Langfuse correlation across all embeddings
- Exponential backoff retry for SQS batch failures

**Configuration:**
```bash
EMBEDDING_QUEUE_URL=<sqs-queue-url-fifo>
CMR_URL=https://cmr.earthdata.nasa.gov
```

**Deployment:**
```bash
pip install -r bootstrap/requirements.txt -t bootstrap/package/
cd bootstrap/package && zip -r ../bootstrap.zip . && cd ../.. && zip -j bootstrap/bootstrap.zip bootstrap/handler.py

# Terraform
cd terraform/application && terraform apply -target=aws_lambda_function.bootstrap_lambda
```

**Example Invocations:**
```bash
# Bootstrap EOSDIS collections with granules (dry-run)
aws lambda invoke \
  --function-name earthdata-mcp-bootstrap \
  --payload '{"concept_type": "collection", "search_params": {"consortium": "EOSDIS", "has_granules": "true"}, "page_size": 500, "dry_run": true}' \
  response.json

# Bootstrap without dry-run (sends to SQS)
aws lambda invoke \
  --function-name earthdata-mcp-bootstrap \
  --payload '{"concept_type": "collection", "search_params": {"consortium": "EOSDIS"}, "page_size": 1000}' \
  response.json
```

**Response:**
```json
{
  "concept_type": "collection",
  "search_params": {"consortium": "EOSDIS", "has_granules": "true"},
  "total_processed": 1250,
  "total_sent": 1248,
  "total_errors": 2,
  "dry_run": false,
  "langfuse_session_id": "bootstrap-a1b2c3d4"
}
```

**Monitoring:**
- Check `langfuse_session_id` in Langfuse dashboard for full bootstrap trace
- Watch SQS queue depth: should increase then gradually decrease as embedding lambda processes

---

### 4. Migration Lambda (`migration/handler.py`)
**Purpose:** Run database schema migrations with idempotency tracking.

**Triggers:** Manual invocation via AWS Console
**Migrations Location:** `migrations/` directory (ordered by filename)

**Key Features:**
- Idempotency: tracks executed migrations in `schema_migrations` table
- Automatic retry on transient failures
- Rollback on validation errors (stops execution)
- Full SQL file execution for complex schema changes

**Configuration:**
```bash
DATABASE_SECRET_ID=<aws-secrets-manager-secret-id>  # Must contain 'url' key
```

**Deployment:**
```bash
pip install -r migration/requirements.txt -t migration/package/
cd migration/package && zip -r ../migration.zip . && cd ../.. && zip -j migration/migration.zip migration/handler.py

# Terraform
cd terraform/application && terraform apply -target=aws_lambda_function.migration_lambda
```

**Creating New Migrations:**
1. Create SQL file in `migrations/` with leading zero-padded number: `004_add_new_column.sql`
2. Ensure idempotent SQL (e.g., `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`)
3. Invoke lambda:
   ```bash
   aws lambda invoke --function-name earthdata-mcp-migration response.json
   ```

**Migration Tracking:**
```sql
-- View migration history
SELECT migration_name, executed_at FROM schema_migrations ORDER BY executed_at;
```

**Error Handling:**
- Migration errors cause full failure (rolls back transaction)
- Lambda exits with non-zero status code
- Check CloudWatch logs for SQL error details
- Fix SQL file and re-invoke (idempotency prevents re-running successful migrations)

---

## Best Practices

### 1. **Error Handling**
- Use specific exception types (CMRError, EmbeddingError, ValidationError)
- Include context: concept-id, message-id, timestamp
- Don't swallow exceptions - log and re-raise unless intent is to skip

### 2. **Logging**
```python
# ✓ Good
logger.info("Processed %d chunks for %s", len(chunks), concept_id)

# ✗ Avoid
logger.info("Done")
logger.info(f"large_dict: {large_dict}")  # Can exceed log limits
```

### 3. **Configuration**
- All config from environment variables or Secrets Manager
- Validate early in handler, raise ValueError with context
- Use type hints for clarity

### 4. **Monitoring & Observability**
- Instrument with Langfuse for full tracing
- Use structured logging with consistent keys
- Track SQS message attributes for session correlation
- Monitor dead-letter queues

### 5. **Testing**
```bash
# Run all tests
cd ../../ && uv run pytest tests/lambdas/

# Run specific lambda tests
uv run pytest tests/lambdas/test_embedding_handler.py -v

# Dry-run bootstrap before full load
aws lambda invoke --payload '{"dry_run": true, "concept_type": "variable"}' response.json
```

---

## Dependency Management

All lambdas pin major versions for reproducibility:

| Package | Constraint | Reason |
|---------|-----------|--------|
| boto3 | `>=1.35.0,<2.0.0` | AWS SDK compatibility |
| psycopg | `>=3.2.0,<4.0.0` | PostgreSQL driver stability |
| pgvector | `>=0.3.0,<1.0.0` | Vector extension API |
| pydantic | `>=2.0.0,<4.0.0` | Data validation consistency |

When updating dependencies:
1. Test locally: `uv sync`
2. Run test suite: `uv run pytest tests/lambdas/`
3. Terraform plan to verify: `cd terraform/application && terraform plan`
4. Deploy to SIT first, verify before UAT/PROD

---

## Operational Runbooks

### Scaling Issues
**Symptom:** SQS queue backing up, embedding lambda throttled

**Solution:**
1. Check Lambda concurrent execution limit: `aws lambda get-account-settings`
2. Increase if needed: `aws lambda put-account-setting --name lambda-max-concurrency`
3. Monitor Bedrock throttling: check Embedding Lambda CloudWatch metrics
4. If Bedrock throttled, contact AWS TAM for embedding model capacity increase

### Dead Letters
**Symptom:** SQS receives DLQ messages after max retries

**Solution:**
1. Identify message: `aws sqs receive-message --queue-url <dlq-url>`
2. Check embedding lambda logs for the message-id
3. Fix root cause (CMR issue, validation issue, etc)
4. Re-process via bootstrap lambda with same concept-id

### Database Connection Issues
**Symptom:** `psycopg.OperationalError: could not connect to server`

**Solution:**
1. Verify RDS security group allows Lambda security group
2. Check Secrets Manager secret exists and has correct format
3. Verify `DATABASE_SECRET_ID` environment variable set
4. Test connection: `psql --dbname=<url> -c "SELECT 1"`

---

## References

- [AWS Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)
- [Pydantic Validation](https://docs.pydantic.dev/)
- [pgvector Documentation](https://github.com/pgvector/pgvector)
- [Langfuse Integration](https://langfuse.com/docs/integrations/python)
