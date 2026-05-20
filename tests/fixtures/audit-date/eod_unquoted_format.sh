#!/usr/bin/env bash
# Fixture: $() command sub with UNQUOTED format string. SHOULD be flagged.
RUN_DATE=$(date +%Y-%m-%d)
echo "$RUN_DATE"
