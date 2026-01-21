# Data sources for IAM policy ARNs
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# CloudWatch Log Group for migration lambda
resource "aws_cloudwatch_log_group" "migration" {
  name              = "/aws/lambda/${var.environment_name}-earthdata-mcp-migration"
  retention_in_days = 14

  tags = var.tags
}

# IAM role for migration lambda
resource "aws_iam_role" "migration_lambda" {
  name        = "${var.environment_name}-earthdata-mcp-migration-role"
  description = "IAM role for the migration lambda function"

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

resource "aws_iam_role_policy" "migration_lambda" {
  name = "${var.environment_name}-earthdata-mcp-migration-policy"
  role = aws_iam_role.migration_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsManager"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = aws_secretsmanager_secret.database.arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.migration.arn}:*"
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

# Security group for migration lambda (VPC access to RDS)
resource "aws_security_group" "migration_lambda" {
  name_prefix = "${var.environment_name}-earthdata-mcp-migration-sg-"
  description = "Security group for migration lambda VPC access"
  vpc_id      = var.vpc_id

  egress {
    description = "HTTPS outbound (Secrets Manager)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description     = "PostgreSQL to database"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.database.id]
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-migration-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Allow migration lambda to connect to database
resource "aws_security_group_rule" "migration_to_database" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.migration_lambda.id
  security_group_id        = aws_security_group.database.id
  description              = "PostgreSQL from migration lambda"
}

# Migration Lambda - manually invoked to run database migrations
resource "aws_lambda_function" "migration" {
  function_name = "${var.environment_name}-earthdata-mcp-migration"
  description   = "Runs database schema migrations"
  role          = aws_iam_role.migration_lambda.arn
  package_type  = "Image"
  image_uri     = var.migration_lambda_image
  timeout       = var.migration_lambda_timeout
  memory_size   = var.migration_lambda_memory

  environment {
    variables = {
      DATABASE_SECRET_ID = aws_secretsmanager_secret.database.arn
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.migration_lambda.id]
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-migration"
  })

  depends_on = [aws_cloudwatch_log_group.migration]
}
