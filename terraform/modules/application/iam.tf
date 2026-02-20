# Get current AWS account ID
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# IAM role for ingest lambda
resource "aws_iam_role" "ingest_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-ingest-role"
  description = "IAM role for the ingest lambda function"

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

resource "aws_iam_role_policy" "ingest_lambda" {
  name = "${var.environment_name}-earthdata-mcp-ingest-policy"
  role = aws_iam_role.ingest_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SQSReceive"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.ingest.arn
      },
      {
        Sid    = "SQSSend"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.embedding.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.ingest.arn}:*"
      }
    ]
  })
}

# IAM role for embedding lambda
resource "aws_iam_role" "embedding_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-embedding-role"
  description = "IAM role for the embedding lambda function"

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

resource "aws_iam_role_policy" "embedding_lambda" {
  name = "${var.environment_name}-earthdata-mcp-embedding-policy"
  role = aws_iam_role.embedding_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SQSReceive"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.embedding.arn
      },
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
          "arn:aws:bedrock:*:*:inference-profile/us.amazon.titan-embed-*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.embedding.arn}:*"
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
        Sid    = "StepFunctions"
        Effect = "Allow"
        Action = [
          "states:StartExecution"
        ]
        Resource = aws_sfn_state_machine.enrichment.arn
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

# Security group for embedding lambda (VPC access to RDS)
resource "aws_security_group" "embedding_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-embedding-sg"
  description = "Security group for embedding lambda VPC access"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-embedding-sg"
  })
}

# HTTPS egress for CMR, Bedrock, Secrets Manager
resource "aws_security_group_rule" "embedding_https_egress" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.embedding_lambda.id
  description       = "HTTPS outbound (CMR, Bedrock, Secrets Manager)"
}

# Allow embedding lambda to connect to database (direct)
resource "aws_security_group_rule" "embedding_to_database" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = var.database_security_group_id
  security_group_id        = aws_security_group.embedding_lambda.id
  description              = "PostgreSQL to embeddings database"
}

# Allow embedding lambda to connect to RDS Proxy
resource "aws_security_group_rule" "embedding_to_proxy" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = var.database_proxy_security_group_id
  security_group_id        = aws_security_group.embedding_lambda.id
  description              = "PostgreSQL to RDS Proxy"
}

# Allow embedding lambda to connect to Redis
resource "aws_security_group_rule" "embedding_to_redis" {
  type                     = "egress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.redis.id
  security_group_id        = aws_security_group.embedding_lambda.id
  description              = "Redis for caching"
}

# IAM role for bootstrap lambda
resource "aws_iam_role" "bootstrap_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-bootstrap-role"
  description = "IAM role for the bootstrap lambda function"

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

resource "aws_iam_role_policy" "bootstrap_lambda" {
  name = "${var.environment_name}-earthdata-mcp-bootstrap-policy"
  role = aws_iam_role.bootstrap_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SQSSend"
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:SendMessageBatch"
        ]
        Resource = aws_sqs_queue.embedding.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.bootstrap.arn}:*"
      }
    ]
  })
}
