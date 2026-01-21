#!/bin/bash
set -e

# Bamboo task: Run database migrations via Lambda
#
# NOTE: This task should normally be DISABLED. Only enable after deploying
#       the database stack or when new migrations are added.
#
# Prerequisites:
#   - Database stack must be deployed
#   - Application stack must be deployed (migration Lambda)
#
# Bamboo Variables:
# +---------------------------------+----------+---------+
# | Variable                        | Required | Default |
# +---------------------------------+----------+---------+
# | bamboo_ENVIRONMENT_NAME         | Yes      | -       |
# | bamboo_AWS_ACCESS_KEY_ID        | Yes      | -       |
# | bamboo_AWS_SECRET_ACCESS_KEY    | Yes      | -       |
# | bamboo_AWS_DEFAULT_REGION       | No       | us-east-1 |
# +---------------------------------+----------+---------+

# Set AWS credentials from Bamboo variables
export AWS_DEFAULT_REGION="${bamboo_AWS_DEFAULT_REGION:-us-east-1}"
export AWS_ACCESS_KEY_ID="${bamboo_AWS_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="${bamboo_AWS_SECRET_ACCESS_KEY}"

ENVIRONMENT="${bamboo_ENVIRONMENT_NAME}"
LAMBDA_NAME="${ENVIRONMENT}-earthdata-mcp-migration"

echo "Running database migrations"
echo "Environment: $ENVIRONMENT"
echo "Lambda: $LAMBDA_NAME"

echo "Invoking migration Lambda..."
RESPONSE=$(aws lambda invoke \
    --function-name "$LAMBDA_NAME" \
    --payload '{}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/migration-response.json)

echo "Lambda response: $RESPONSE"

# Check for function errors
if echo "$RESPONSE" | grep -q '"FunctionError"'; then
    echo "ERROR: Lambda function returned an error"
    cat /tmp/migration-response.json
    exit 1
fi

# Display the response
echo "Migration result:"
cat /tmp/migration-response.json
echo ""

# Check for success in response body
if grep -q '"message": "Migrations completed"' /tmp/migration-response.json; then
    echo "All migrations completed successfully"
elif grep -q '"message": "No migrations found"' /tmp/migration-response.json; then
    echo "No migrations to run"
else
    echo "ERROR: Unexpected response from migration Lambda"
    exit 1
fi
