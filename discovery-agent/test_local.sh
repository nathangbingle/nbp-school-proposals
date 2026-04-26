#!/usr/bin/env bash
# Quick smoke test for the enrichment agent.
# Run from this directory:  bash test_local.sh
#
# You need ANTHROPIC_API_KEY exported in your shell first:
#   export ANTHROPIC_API_KEY=sk-ant-...

set -e

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "✗ ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

echo "Running smoke test (1 club, dry-run, no commit)..."
echo "------------------------------------------------"

DRY_RUN=1 MAX_CLUBS=1 python3 enrich.py
