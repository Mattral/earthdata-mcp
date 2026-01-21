# Data sources for VPC and subnets
data "aws_vpc" "main" {
  filter {
    name   = "tag:Name"
    values = [var.vpc_tag_name_filter]
  }
}

data "aws_subnets" "main" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }

  tags = {
    Name = var.subnet_tag_name_filter
  }
}

# ECR Repository for migration lambda
resource "aws_ecr_repository" "migration_lambda" {
  name                 = "${var.environment_name}-earthdata-mcp-migration"
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Name = "${var.environment_name}-earthdata-mcp-migration"
  })
}

resource "aws_ecr_lifecycle_policy" "migration_lambda" {
  repository = aws_ecr_repository.migration_lambda.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only 5 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = {
        type = "expire"
      }
    }]
  })
}

# Database module
module "database" {
  source = "../modules/database"

  environment_name  = var.environment_name
  vpc_id            = data.aws_vpc.main.id
  subnet_ids        = data.aws_subnets.main.ids
  availability_zone = var.availability_zone

  instance_class        = var.instance_class
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  engine_version        = var.engine_version
  database_name         = var.database_name
  master_username       = var.master_username

  backup_retention_period = var.backup_retention_period
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = var.skip_final_snapshot

  # Migration Lambda
  migration_lambda_image = "${aws_ecr_repository.migration_lambda.repository_url}:${var.image_tag}"

  tags = var.tags
}
