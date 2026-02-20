# =============================================================================
# ENRICHMENT PIPELINE LAMBDA AND STEP FUNCTION
# =============================================================================

# CloudWatch Log Group for enrichment lambda
resource "aws_cloudwatch_log_group" "enrichment" {
  name              = "/aws/lambda/${var.environment_name}-earthdata-mcp-enrichment"
  retention_in_days = 14
  tags              = var.tags
}

# -----------------------------------------------------------------------------
# IAM Role for Enrichment Lambda
# -----------------------------------------------------------------------------
resource "aws_iam_role" "enrichment_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-enrichment-role"
  description = "IAM role for enrichment pipeline lambda functions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "enrichment_lambda" {
  name = "${var.environment_name}-earthdata-mcp-enrichment-policy"
  role = aws_iam_role.enrichment_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsManager"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          var.database_secret_arn,
          aws_secretsmanager_secret.redis.arn
        ]
      },
      {
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-*",
          "arn:aws:bedrock:*::foundation-model/amazon.titan-text-*",
          "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
          "arn:aws:bedrock:*:*:inference-profile/us.amazon.titan-*",
          "arn:aws:bedrock:*:*:inference-profile/us.amazon.nova-*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = [
          "${aws_cloudwatch_log_group.enrichment.arn}:*"
        ]
      },
      {
        Sid    = "SSMGetParameter"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.environment_name}-langfuse-secret-key"
      },
      {
        Sid    = "KMSDecryptSSM"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = "arn:aws:kms:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm"
      },
      {
        Sid    = "VPCNetworkInterfaces"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses"
        ]
        Resource = "*"
      }
    ]
  })
}

# Security group for enrichment lambda (same as embedding lambda)
resource "aws_security_group" "enrichment_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-enrichment-sg"
  description = "Security group for enrichment lambda VPC access"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-enrichment-sg"
  })
}

resource "aws_security_group_rule" "enrichment_https_egress" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.enrichment_lambda.id
  description       = "HTTPS outbound (CMR, KMS, Bedrock, Secrets Manager)"
}

resource "aws_security_group_rule" "enrichment_http_egress" {
  type              = "egress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.enrichment_lambda.id
  description       = "HTTP outbound for URL validation"
}

resource "aws_security_group_rule" "enrichment_to_database" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = var.database_security_group_id
  security_group_id        = aws_security_group.enrichment_lambda.id
  description              = "PostgreSQL to database"
}

resource "aws_security_group_rule" "enrichment_to_proxy" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = var.database_proxy_security_group_id
  security_group_id        = aws_security_group.enrichment_lambda.id
  description              = "PostgreSQL to RDS Proxy"
}

resource "aws_security_group_rule" "enrichment_to_redis" {
  type                     = "egress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.redis.id
  security_group_id        = aws_security_group.enrichment_lambda.id
  description              = "Redis for caching"
}

resource "aws_security_group_rule" "redis_from_enrichment" {
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.enrichment_lambda.id
  security_group_id        = aws_security_group.redis.id
  description              = "Redis from enrichment lambda"
}

# -----------------------------------------------------------------------------
# Enrichment Lambda Function (single router)
# -----------------------------------------------------------------------------

# Common environment variables for enrichment lambda
locals {
  enrichment_env_vars = {
    ENVIRONMENT_NAME    = var.environment_name
    CMR_URL             = var.cmr_url
    DATABASE_SECRET_ID  = var.database_secret_arn
    DB_HOST             = var.database_proxy_endpoint
    EMBEDDINGS_TABLE    = var.embeddings_table
    ASSOCIATIONS_TABLE  = var.associations_table
    LANGFUSE_BASE_URL   = var.langfuse_host
    LANGFUSE_PUBLIC_KEY = var.langfuse_public_key
    REDIS_SECRET_ID     = aws_secretsmanager_secret.redis.arn
  }
}

resource "aws_lambda_function" "enrichment" {
  function_name = "${var.environment_name}-earthdata-mcp-enrichment"
  description   = "Enrichment pipeline router - dispatches to sub-handlers by action"
  role          = aws_iam_role.enrichment_lambda.arn
  package_type  = "Image"
  image_uri     = var.enrichment_lambda_image
  timeout       = 300
  memory_size   = 512

  reserved_concurrent_executions = var.enrichment_lambda_concurrency

  image_config {
    command = ["lambdas.enrichment.handler.handler"]
  }

  environment {
    variables = local.enrichment_env_vars
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.enrichment_lambda.id]
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-enrichment"
  })

  depends_on = [aws_cloudwatch_log_group.enrichment]
}

# -----------------------------------------------------------------------------
# Step Function
# -----------------------------------------------------------------------------

resource "aws_iam_role" "enrichment_step_function" {
  name        = "${var.environment_name}-earthdata-mcp-enrichment-sfn-role"
  description = "IAM role for enrichment Step Function"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "enrichment_step_function" {
  name = "${var.environment_name}-earthdata-mcp-enrichment-sfn-policy"
  role = aws_iam_role.enrichment_step_function.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeLambda"
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = [
          aws_lambda_function.enrichment.arn
        ]
      },
      {
        Sid    = "CloudWatchLogs"
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
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "enrichment_step_function" {
  name              = "/aws/states/${var.environment_name}-earthdata-mcp-enrichment"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_sfn_state_machine" "enrichment" {
  name     = "${var.environment_name}-earthdata-mcp-enrichment"
  role_arn = aws_iam_role.enrichment_step_function.arn

  definition = templatefile("${path.module}/step_function_definition.json", {
    enrichment_lambda_arn = aws_lambda_function.enrichment.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.enrichment_step_function.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-enrichment"
  })
}
