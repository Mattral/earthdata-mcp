"""Pytest configuration and fixtures."""

import json
import os
from pathlib import Path

# Set test environment variables before any imports that might use them
os.environ.setdefault("REDIS_SSL", "false")
os.environ.setdefault("REDIS_HOST", "localhost")

# Disable OpenTelemetry during tests to prevent connection errors to localhost:3000
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

MOCKS_DIR = Path(__file__).parent / "mocks"

# Shared test data for spatial extent tests
GLOBAL_BOUNDING_BOX = {
    "WestBoundingCoordinate": -180.0,
    "EastBoundingCoordinate": 180.0,
    "NorthBoundingCoordinate": 90.0,
    "SouthBoundingCoordinate": -90.0,
}


def load_mock(category: str, name: str) -> dict:
    """Load a mock JSON file from tests/mocks/{category}/{name}.json"""
    path = MOCKS_DIR / category / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)
