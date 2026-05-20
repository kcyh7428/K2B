#!/usr/bin/env bash
# Fixture: classic quoted form, already caught by pre-existing pattern.
# Kept as a regression sanity check that the new alternations did not break
# the original detection.
RUN_DATE=$(date '+%Y-%m-%d')
echo "$RUN_DATE"
