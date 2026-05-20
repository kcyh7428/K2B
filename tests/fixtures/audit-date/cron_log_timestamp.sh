#!/usr/bin/env bash
# Fixture: full ISO timestamp (date-and-time), NOT date bucketing. NOT flagged.
# %Y-%m-%dT%H:%M:%S has %Y-%m-%d followed by T, which is not a bucket terminator.
TS=$(date '+%Y-%m-%dT%H:%M:%S')
TS2=`date '+%Y-%m-%dT%H:%M:%S'`
echo "$TS $TS2"
