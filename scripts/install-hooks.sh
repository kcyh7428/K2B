#!/usr/bin/env bash
# One-time install of repo-tracked git hooks. Idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

git config core.hooksPath .githooks
chmod 755 .githooks/pre-commit
chmod 755 .githooks/commit-msg

echo "install-hooks: core.hooksPath now points to .githooks"
echo "install-hooks: pre-commit and commit-msg are executable"
