# =============================================================================
# RDS PROXY FOR CONNECTION POOLING
# =============================================================================

# -----------------------------------------------------------------------------
# IAM Role for RDS Proxy to read Secrets Manager
# -----------------------------------------------------------------------------

resource "aws_iam_role" "rds_proxy" {
  name        = "${var.environment_name}-earthdata-mcp-rds-proxy-role"
  description = "IAM role for RDS Proxy to access Secrets Manager"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "rds.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "rds_proxy_secrets" {
  name = "${var.environment_name}-earthdata-mcp-rds-proxy-secrets"
  role = aws_iam_role.rds_proxy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsManagerAccess"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = aws_secretsmanager_secret.database.arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Security Group for RDS Proxy
# -----------------------------------------------------------------------------

resource "aws_security_group" "rds_proxy" {
  name        = "${var.environment_name}-earthdata-mcp-rds-proxy-sg"
  description = "Security group for RDS Proxy"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-rds-proxy-sg"
  })
}

# Proxy -> RDS egress
resource "aws_security_group_rule" "proxy_to_rds" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.database.id
  security_group_id        = aws_security_group.rds_proxy.id
  description              = "PostgreSQL to RDS instance"
}

# RDS <- Proxy ingress
resource "aws_security_group_rule" "rds_from_proxy" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.rds_proxy.id
  security_group_id        = aws_security_group.database.id
  description              = "PostgreSQL from RDS Proxy"
}

# -----------------------------------------------------------------------------
# RDS Proxy
# -----------------------------------------------------------------------------

resource "aws_db_proxy" "main" {
  name                   = "${var.environment_name}-earthdata-mcp-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_subnet_ids         = var.subnet_ids
  vpc_security_group_ids = [aws_security_group.rds_proxy.id]
  require_tls            = true

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "DISABLED"
    secret_arn  = aws_secretsmanager_secret.database.arn
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-proxy"
  })
}

resource "aws_db_proxy_default_target_group" "main" {
  db_proxy_name = aws_db_proxy.main.name

  connection_pool_config {
    max_connections_percent      = 100
    connection_borrow_timeout    = 120
  }
}

resource "aws_db_proxy_target" "main" {
  db_proxy_name          = aws_db_proxy.main.name
  target_group_name      = aws_db_proxy_default_target_group.main.name
  db_instance_identifier = aws_db_instance.main.identifier
}
