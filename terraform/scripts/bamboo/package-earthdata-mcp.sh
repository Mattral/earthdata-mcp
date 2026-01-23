#!/bin/bash
set -e

# Bamboo task: Package source code for deployment
#
# Creates a tarball of the project that can be downloaded by deployment plans.
# Working directory should be the parent of earthdata-mcp/

echo "Packaging source code for deployment..."

tar -czf earthdata-mcp-deployed-package.tgz \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='htmlcov' \
  --exclude='.terraform' \
  --exclude='*.tfstate*' \
  earthdata-mcp

echo "Created earthdata-mcp-deployed-package.tgz"
ls -lh earthdata-mcp-deployed-package.tgz
