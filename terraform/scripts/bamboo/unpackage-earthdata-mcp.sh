#!/bin/bash
set -e

# Bamboo task: Extract deployment package
#
# Extracts the tarball created by package-earthdata-mcp.sh
# Run this once before any deploy scripts.

echo "Extracting deployment package..."
tar -xzf earthdata-mcp-deployed-package.tgz
echo "Extraction complete"
