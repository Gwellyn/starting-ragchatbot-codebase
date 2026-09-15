#!/bin/bash
# Verify the backend codebase is black-formatted, without modifying files.
# Exits non-zero if formatting is needed (suitable for CI).
set -e

cd "$(dirname "$0")/.."

echo "Checking formatting with black..."
uv run black --check --diff backend main.py
