# CloudWatch Log Groups (created before lambdas for IAM policy reference)
resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${var.environment_name}-earthdata-mcp-ingest"
  retention_in_days = 14

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "embedding" {
  name              = "/aws/lambda/${var.environment_name}-earthdata-mcp-embedding"
  retention_in_days = 14

  tags = var.tags
}

# Ingest Lambda - container-based
resource "aws_lambda_function" "ingest" {
  function_name = "${var.environment_name}-earthdata-mcp-ingest"
  description   = "Receives metadata updates from SNS and queues them for embedding generation"
  role          = aws_iam_role.ingest_lambda.arn
  package_type  = "Image"
  image_uri     = var.ingest_lambda_image
  timeout       = var.ingest_lambda_timeout
  memory_size   = var.ingest_lambda_memory

  reserved_concurrent_executions = var.ingest_lambda_concurrency

  environment {
    variables = {
      EMBEDDING_QUEUE_URL = aws_sqs_queue.embedding.url
    }
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-ingest"
  })

  depends_on = [aws_cloudwatch_log_group.ingest]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn = aws_sqs_queue.ingest.arn
  function_name    = aws_lambda_function.ingest.arn
  batch_size       = 10
  enabled          = true

  function_response_types = ["ReportBatchItemFailures"]
}

# Embedding Lambda - container-based, VPC-connected
resource "aws_lambda_function" "embedding" {
  function_name = "${var.environment_name}-earthdata-mcp-embedding"
  description   = "Generates vector embeddings and stores them for querying"
  role          = aws_iam_role.embedding_lambda.arn
  package_type  = "Image"
  image_uri     = var.embedding_lambda_image
  timeout       = var.embedding_lambda_timeout
  memory_size   = var.embedding_lambda_memory

  reserved_concurrent_executions = var.embedding_lambda_concurrency

  environment {
    variables = {
      ENVIRONMENT_NAME    = var.environment_name
      CMR_URL             = var.cmr_url
      DATABASE_SECRET_ID  = var.database_secret_arn
      EMBEDDINGS_TABLE    = var.embeddings_table
      ASSOCIATIONS_TABLE  = var.associations_table
      LANGFUSE_BASE_URL   = var.langfuse_host
      LANGFUSE_PUBLIC_KEY = var.langfuse_public_key
      REDIS_SECRET_ID     = aws_secretsmanager_secret.redis.arn
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.embedding_lambda.id]
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-embedding"
  })

  depends_on = [aws_cloudwatch_log_group.embedding]
}

resource "aws_lambda_event_source_mapping" "embedding" {
  event_source_arn = aws_sqs_queue.embedding.arn
  function_name    = aws_lambda_function.embedding.arn
  batch_size       = 10

  function_response_types = ["ReportBatchItemFailures"]

  enabled = true
}

# Bootstrap Lambda - manually invoked for bulk loading
resource "aws_cloudwatch_log_group" "bootstrap" {
  name              = "/aws/lambda/${var.environment_name}-earthdata-mcp-bootstrap"
  retention_in_days = 14

  tags = var.tags
}

resource "aws_lambda_function" "bootstrap" {
  function_name = "${var.environment_name}-earthdata-mcp-bootstrap"
  description   = "Manually invoked to bulk-load metadata into the embedding queue"
  role          = aws_iam_role.bootstrap_lambda.arn
  package_type  = "Image"
  image_uri     = var.bootstrap_lambda_image
  timeout       = var.bootstrap_lambda_timeout
  memory_size   = var.bootstrap_lambda_memory

  environment {
    variables = {
      CMR_URL             = var.cmr_url
      EMBEDDING_QUEUE_URL = aws_sqs_queue.embedding.url
    }
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-bootstrap"
  })

  depends_on = [aws_cloudwatch_log_group.bootstrap]
}
