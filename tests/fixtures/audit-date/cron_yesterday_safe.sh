#!/usr/bin/env bash
# Fixture: explicit -v-1d means this script is yesterday-aware. NOT flagged.
RUN_DATE=$(date -v-1d '+%Y-%m-%d')
PREV_WK=`date -v-7d '+%Y-%m-%d'`
echo "$RUN_DATE $PREV_WK"
