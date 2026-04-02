#!/bin/bash
# vault-query.sh -- Query Obsidian vault via Local REST API
#
# Usage:
#   vault-query.sh dql 'TABLE type, status FROM "Inbox" WHERE review-action != ""'
#   vault-query.sh search "recruitment transformation"
#   vault-query.sh status

set -euo pipefail

API_URL="https://127.0.0.1:27124"
API_KEY="${OBSIDIAN_API_KEY:-}"

if [[ -z "$API_KEY" ]]; then
  echo "Error: OBSIDIAN_API_KEY not set" >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $API_KEY"

check_api() {
  local status
  status=$(curl --insecure -s -o /dev/null -w "%{http_code}" --max-time 3 "$API_URL/" -H "$AUTH_HEADER" 2>/dev/null) || true
  if [[ "$status" != "200" ]]; then
    echo "Error: Obsidian Local REST API unreachable (is Obsidian running?)" >&2
    exit 2
  fi
}

case "${1:-help}" in
  dql)
    shift
    check_api
    curl --insecure -s -X POST "$API_URL/search/" \
      -H "$AUTH_HEADER" \
      -H "Content-Type: application/vnd.olrapi.dataview.dql+txt" \
      -d "$1"
    ;;

  search)
    shift
    check_api
    curl --insecure -s -X POST "$API_URL/search/simple/?query=$(printf '%s' "$1" | jq -sRr @uri)" \
      -H "$AUTH_HEADER" \
      -H "Accept: application/json"
    ;;

  status)
    check_api
    curl --insecure -s "$API_URL/" -H "$AUTH_HEADER"
    ;;

  help|*)
    echo "Usage: vault-query.sh <command> [args]"
    echo ""
    echo "Commands:"
    echo "  dql <query>      Run a Dataview DQL query"
    echo "  search <term>    Full-text search across vault"
    echo "  status           Check API status"
    echo ""
    echo "Examples:"
    echo "  vault-query.sh dql 'TABLE type, status FROM \"Inbox\" WHERE review-action != \"\"'"
    echo "  vault-query.sh search \"recruitment transformation\""
    ;;
esac
