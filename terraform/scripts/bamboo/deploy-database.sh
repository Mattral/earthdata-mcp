#!/bin/bash
set -e

# Bamboo task: Deploy database stack (RDS, Secrets Manager, Migration Lambda)
#
# This task deploys the database infrastructure. Run this before
# deploying the application stack.
#
# Bamboo Variables:
# +---------------------------------+----------+--------------------------------+
# | Variable                        | Required | Default                        |
# +---------------------------------+----------+--------------------------------+
# | bamboo_ENVIRONMENT_NAME         | Yes      | -                              |
# | bamboo_VPC_TAG_NAME_FILTER      | Yes      | -                              |
# | bamboo_SUBNET_TAG_NAME_FILTER   | Yes      | -                              |
# | bamboo_RELEASE_VERSION          | Yes      | -                              |
# | bamboo_AWS_ACCESS_KEY_ID        | Yes      | -                              |
# | bamboo_AWS_SECRET_ACCESS_KEY    | Yes      | -                              |
# | bamboo_AWS_DEFAULT_REGION       | No       | us-east-1                      |
# | bamboo_DB_INSTANCE_CLASS        | No       | db.t3.medium                   |
# | bamboo_ALLOCATED_STORAGE        | No       | 20                             |
# | bamboo_MAX_ALLOCATED_STORAGE    | No       | 100                            |
# | bamboo_DB_ENGINE_VERSION        | No       | 17.4                           |
# | bamboo_DELETION_PROTECTION      | No       | true                           |
# +---------------------------------+----------+--------------------------------+

# Set AWS credentials from Bamboo variables
export AWS_DEFAULT_REGION="${bamboo_AWS_DEFAULT_REGION:-us-east-1}"
export AWS_ACCESS_KEY_ID="${bamboo_AWS_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="${bamboo_AWS_SECRET_ACCESS_KEY}"

cd earthdata-mcp/terraform

ENVIRONMENT="${bamboo_ENVIRONMENT_NAME}"
AWS_REGION="${AWS_DEFAULT_REGION}"
IMAGE_TAG="${bamboo_RELEASE_VERSION}"

# Export required terraform variables
export TF_VAR_environment_name="$ENVIRONMENT"
export TF_VAR_aws_region="$AWS_REGION"
export TF_VAR_vpc_tag_name_filter="${bamboo_VPC_TAG_NAME_FILTER}"
export TF_VAR_subnet_tag_name_filter="${bamboo_SUBNET_TAG_NAME_FILTER}"
export TF_VAR_image_tag="$IMAGE_TAG"

# Export optional terraform variables if set
[ -n "$bamboo_DB_INSTANCE_CLASS" ] && export TF_VAR_instance_class="$bamboo_DB_INSTANCE_CLASS"
[ -n "$bamboo_ALLOCATED_STORAGE" ] && export TF_VAR_allocated_storage="$bamboo_ALLOCATED_STORAGE"
[ -n "$bamboo_MAX_ALLOCATED_STORAGE" ] && export TF_VAR_max_allocated_storage="$bamboo_MAX_ALLOCATED_STORAGE"
[ -n "$bamboo_DB_ENGINE_VERSION" ] && export TF_VAR_engine_version="$bamboo_DB_ENGINE_VERSION"
[ -n "$bamboo_DELETION_PROTECTION" ] && export TF_VAR_deletion_protection="$bamboo_DELETION_PROTECTION"

SCRIPTS_DIR="scripts"
STACK_DIR="database"

echo ""
echo "Deploying database stack"
echo "Environment: $ENVIRONMENT"
echo "Region: $AWS_REGION"
echo "Image tag: $IMAGE_TAG"

cd "$STACK_DIR"

# Initialize Terraform
terraform init -input=false -no-color -reconfigure \
  -backend-config="bucket=tf-state-cmr-${ENVIRONMENT}" \
  -backend-config="key=earthdata-mcp/database-${ENVIRONMENT}" \
  -backend-config="region=${AWS_REGION}"

# Step 1: Create ECR repository first
echo ""
echo "Creating ECR repository..."
terraform apply -no-color -auto-approve \
  -target=aws_ecr_repository.migration_lambda

# Step 2: Build and push Docker image
echo ""
echo "Building and pushing migration Lambda image..."
cd ..
./"$SCRIPTS_DIR"/docker-build.sh MigrationLambdaDockerfile "$ENVIRONMENT" "$IMAGE_TAG"

# Step 3: Deploy everything else
echo ""
echo "Deploying database infrastructure..."
cd "$STACK_DIR"
terraform apply -no-color -auto-approve

echo ""
echo "Database stack deployed successfully"
