#!/usr/bin/env bash
# Bot attachment-path smoke test: ensures the TS build is green and the
# unit tests that guard the Ship 1B code paths all pass.
#
# This is the cross-language integration guard. If someone refactors the
# pending-confirmation JSON schema on the Python/shell side without
# updating the TS side (or vice versa), the relevant vitest suites fail
# and this script exits non-zero.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO/k2b-remote"

echo "=== typecheck ==="
npm run typecheck

echo
echo "=== attachmentIngest + resume + gate Ship 1B tests ==="
npm test -- --run \
  src/attachmentIngest.test.ts \
  src/washingMachineResume.test.ts \
  src/washingMachine.gate.test.ts

echo
echo "=== shell-level pending-confirm contract ==="
bash "$REPO/tests/washing-machine/pending-confirm.test.sh"

echo
echo "=== extract-attachment dispatcher ==="
bash "$REPO/tests/washing-machine/extract-attachment.test.sh"

echo
echo "Ship 1B bot-attachment-path smoke: all suites green."
