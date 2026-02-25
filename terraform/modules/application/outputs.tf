output "ingest_queue_arn" {
  description = "ARN of the ingest SQS queue"
  value       = aws_sqs_queue.ingest.arn
}

output "ingest_queue_url" {
  description = "URL of the ingest SQS queue"
  value       = aws_sqs_queue.ingest.url
}

output "ingest_dlq_arn" {
  description = "ARN of the ingest dead letter queue"
  value       = aws_sqs_queue.ingest_dlq.arn
}

output "embedding_queue_arn" {
  description = "ARN of the embedding FIFO queue"
  value       = aws_sqs_queue.embedding.arn
}

output "embedding_queue_url" {
  description = "URL of the embedding FIFO queue"
  value       = aws_sqs_queue.embedding.url
}

output "embedding_dlq_arn" {
  description = "ARN of the embedding dead letter queue"
  value       = aws_sqs_queue.embedding_dlq.arn
}

output "ingest_lambda_arn" {
  description = "ARN of the ingest Lambda function"
  value       = aws_lambda_function.ingest.arn
}

output "ingest_lambda_name" {
  description = "Name of the ingest Lambda function"
  value       = aws_lambda_function.ingest.function_name
}

output "embedding_lambda_arn" {
  description = "ARN of the embedding Lambda function"
  value       = aws_lambda_function.embedding.arn
}

output "embedding_lambda_name" {
  description = "Name of the embedding Lambda function"
  value       = aws_lambda_function.embedding.function_name
}

output "embedding_lambda_security_group_id" {
  description = "Security group ID for the embedding Lambda"
  value       = aws_security_group.embedding_lambda.id
}

output "mcp_server_security_group_id" {
  description = "Security group ID for the MCP server"
  value       = aws_security_group.mcp_server.id
}

output "enrichment_state_machine_arn" {
  description = "ARN of the enrichment Step Function state machine"
  value       = aws_sfn_state_machine.enrichment.arn
}

output "enrichment_state_machine_name" {
  description = "Name of the enrichment Step Function state machine"
  value       = aws_sfn_state_machine.enrichment.name
}

output "enrichment_lambda_security_group_id" {
  description = "Security group ID for the enrichment Lambdas"
  value       = aws_security_group.enrichment_lambda.id
}
