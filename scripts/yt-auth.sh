#!/usr/bin/env bash
# One-time OAuth2 authorization for YouTube Data API
# Saves refresh token to ~/.config/k2b/youtube-token.json
set -euo pipefail

CLIENT_SECRET_FILE="${HOME}/.config/gws/client_secret.json"
TOKEN_DIR="${HOME}/.config/k2b"
TOKEN_FILE="${TOKEN_DIR}/youtube-token.json"
REDIRECT_PORT=8085
REDIRECT_URI="http://localhost:${REDIRECT_PORT}"
SCOPE="https://www.googleapis.com/auth/youtube"

if [[ ! -f "$CLIENT_SECRET_FILE" ]]; then
  echo "ERROR: Client secret not found at ${CLIENT_SECRET_FILE}" >&2
  exit 1
fi

# Read client credentials
CLIENT_ID=$(jq -r '(.installed // .web).client_id' "$CLIENT_SECRET_FILE")
CLIENT_SECRET=$(jq -r '(.installed // .web).client_secret' "$CLIENT_SECRET_FILE")

if [[ -z "$CLIENT_ID" || "$CLIENT_ID" == "null" ]]; then
  echo "ERROR: Could not read client_id from ${CLIENT_SECRET_FILE}" >&2
  exit 1
fi

mkdir -p "$TOKEN_DIR"

# Cleanup on exit (kill server, remove temp files)
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  rm -rf "$TMPDIR_SERVER" 2>/dev/null || true
}
trap cleanup EXIT

# Build authorization URL
AUTH_URL="https://accounts.google.com/o/oauth2/v2/auth?client_id=${CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=${SCOPE}&access_type=offline&prompt=consent"

echo "Opening browser for Google OAuth authorization..."
echo "If the browser doesn't open, visit this URL:"
echo "$AUTH_URL"
echo ""

# Open browser
open "$AUTH_URL" 2>/dev/null || xdg-open "$AUTH_URL" 2>/dev/null || true

# Create a temporary Python HTTP server to capture the redirect
AUTH_CODE=""
TMPDIR_SERVER=$(mktemp -d)
FIFO="${TMPDIR_SERVER}/code_fifo"
mkfifo "$FIFO"

# Python HTTP server that captures the auth code and responds with a success page
python3 -c "
import http.server
import urllib.parse
import sys
import os

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        code = params.get('code', [None])[0]
        error = params.get('error', [None])[0]

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()

        if code:
            self.wfile.write(b'<html><body><h2>Authorization successful!</h2><p>You can close this tab.</p></body></html>')
            with open('${FIFO}', 'w') as f:
                f.write(code)
        elif error:
            self.wfile.write(f'<html><body><h2>Authorization failed: {error}</h2></body></html>'.encode())
            with open('${FIFO}', 'w') as f:
                f.write('ERROR:' + error)
        else:
            self.wfile.write(b'<html><body><h2>No code received</h2></body></html>')
            with open('${FIFO}', 'w') as f:
                f.write('ERROR:no_code')

    def log_message(self, format, *args):
        pass  # Suppress server logs

server = http.server.HTTPServer(('localhost', ${REDIRECT_PORT}), Handler)
server.handle_request()
" &
SERVER_PID=$!

# Wait for the auth code from the FIFO
AUTH_CODE=$(cat "$FIFO")
rm -rf "$TMPDIR_SERVER" 2>/dev/null || true

# Kill the server if still running (trap will also handle this)
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true

if [[ "$AUTH_CODE" == ERROR:* ]]; then
  echo "ERROR: Authorization failed: ${AUTH_CODE#ERROR:}" >&2
  exit 1
fi

if [[ -z "$AUTH_CODE" ]]; then
  echo "ERROR: No authorization code received" >&2
  exit 1
fi

echo "Authorization code received. Exchanging for tokens..."

# Exchange auth code for tokens
TOKEN_RESPONSE=$(/usr/bin/curl --silent --show-error \
  --fail-with-body \
  -X POST "https://oauth2.googleapis.com/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "code=${AUTH_CODE}" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  -d "redirect_uri=${REDIRECT_URI}" \
  -d "grant_type=authorization_code") || {
    echo "ERROR: Token exchange failed" >&2
    echo "$TOKEN_RESPONSE" >&2
    exit 1
  }

# Check for refresh token
REFRESH_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.refresh_token // empty')
if [[ -z "$REFRESH_TOKEN" ]]; then
  echo "ERROR: No refresh_token in response. Try revoking access at https://myaccount.google.com/permissions and re-running." >&2
  echo "$TOKEN_RESPONSE" >&2
  exit 1
fi

# Save token file
echo "$TOKEN_RESPONSE" | jq '{refresh_token: .refresh_token, scope: .scope, token_type: .token_type}' > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

echo "Success! Refresh token saved to ${TOKEN_FILE}"
