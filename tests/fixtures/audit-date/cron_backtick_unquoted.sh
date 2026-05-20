#!/usr/bin/env bash
# Fixture: backtick command sub with unquoted format string. SHOULD be flagged.
RUN_DATE=`date +%Y-%m-%d`
echo "$RUN_DATE"
