#!/usr/bin/env python3
"""YouTube search via YouTube Data API v3.

Uses K2B's existing OAuth credentials (same as yt-playlist-add.sh).
Returns structured results: title, channel, views, duration, date, URL.

Usage:
  yt-search.py <query> [--count N] [--months N] [--no-date-filter] [--json]

Examples:
  yt-search.py "claude code trading" --count 10 --months 3
  yt-search.py "AI investment research" --json
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError


TOKEN_FILE = Path.home() / ".config" / "k2b" / "youtube-token.json"
CLIENT_SECRET_FILE = Path.home() / ".config" / "gws" / "client_secret.json"
API_BASE = "https://www.googleapis.com/youtube/v3"


def parse_args(argv):
    args = argv[1:]
    count = 20
    months = 6
    as_json = False
    query_parts = []
    i = 0
    while i < len(args):
        if args[i] == "--count" and i + 1 < len(args):
            count = int(args[i + 1])
            i += 2
        elif args[i] == "--months" and i + 1 < len(args):
            months = int(args[i + 1])
            i += 2
        elif args[i] == "--no-date-filter":
            months = 0
            i += 1
        elif args[i] == "--json":
            as_json = True
            i += 1
        else:
            query_parts.append(args[i])
            i += 1
    query = " ".join(query_parts)
    if not query:
        print("Usage: yt-search.py <query> [--count N] [--months N] [--no-date-filter] [--json]", file=sys.stderr)
        sys.exit(1)
    return query, count, months, as_json


def get_access_token():
    """Refresh OAuth access token using stored credentials."""
    if not TOKEN_FILE.exists():
        print(f"ERROR: Token file not found at {TOKEN_FILE}. Run yt-auth.sh first.", file=sys.stderr)
        sys.exit(1)
    if not CLIENT_SECRET_FILE.exists():
        print(f"ERROR: Client secret not found at {CLIENT_SECRET_FILE}.", file=sys.stderr)
        sys.exit(1)

    try:
        token_data = json.loads(TOKEN_FILE.read_text())
        client_data = json.loads(CLIENT_SECRET_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: Failed to read credentials: {e}", file=sys.stderr)
        sys.exit(1)

    creds = client_data.get("installed") or client_data.get("web")
    if not creds:
        print("ERROR: No 'installed' or 'web' key in client_secret.json.", file=sys.stderr)
        sys.exit(1)

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh_token in token file. Run yt-auth.sh to re-authorize.", file=sys.stderr)
        sys.exit(1)

    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    if not client_id or not client_secret:
        print("ERROR: Missing client_id or client_secret in client_secret.json.", file=sys.stderr)
        sys.exit(1)

    data = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urlopen(req) as resp:
            result = json.loads(resp.read())
            return result["access_token"]
    except HTTPError as e:
        body = e.read().decode()
        print(f"ERROR: Token refresh failed: {e.code} {body}", file=sys.stderr)
        sys.exit(1)


def youtube_search(query, count, months, access_token):
    """Search YouTube via Data API v3."""
    if count > 50:
        print(f"WARNING: YouTube API caps at 50 results per query. Returning 50 instead of {count}.", file=sys.stderr)
        count = 50

    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": count,
        "order": "relevance",
    }

    if months > 0:
        after = datetime.now(timezone.utc) - timedelta(days=months * 30)
        params["publishedAfter"] = after.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{API_BASE}/search?{urlencode(params)}"
    req = Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode()
        print(f"ERROR: YouTube search failed: {e.code} {body}", file=sys.stderr)
        sys.exit(1)


def get_video_details(video_ids, access_token):
    """Fetch view counts, duration, channel stats for videos."""
    if not video_ids:
        return {}

    params = {
        "part": "statistics,contentDetails,snippet",
        "id": ",".join(video_ids),
    }

    url = f"{API_BASE}/videos?{urlencode(params)}"
    req = Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urlopen(req) as resp:
            data = json.loads(resp.read())
            return {item["id"]: item for item in data.get("items", [])}
    except HTTPError as e:
        print(f"WARNING: Failed to fetch video details (HTTP {e.code}). View counts and durations will show N/A.", file=sys.stderr)
        return {}


def parse_duration(iso_duration):
    """Convert ISO 8601 duration (P1DT2H3M4S or PT1H2M3S) to human readable."""
    if not iso_duration:
        return "N/A"
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not m:
        return "N/A"
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0) + days * 24
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_views(n):
    if n is None:
        return "N/A"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_date(iso_date):
    if not iso_date:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except (ValueError, AttributeError):
        return iso_date[:10]


def main():
    query, count, months, as_json = parse_args(sys.argv)

    date_label = f", last {months} months" if months > 0 else ""
    print(f'Searching YouTube for: "{query}" (top {count} results{date_label})...\n', file=sys.stderr)

    access_token = get_access_token()
    search_results = youtube_search(query, count, months, access_token)

    items = search_results.get("items", [])
    if not items:
        print("No results found.", file=sys.stderr)
        sys.exit(0)

    # Get detailed stats for all videos in one API call (saves quota)
    video_ids = [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]
    details = get_video_details(video_ids, access_token)

    results = []
    for item in items:
        video_id = item.get("id", {}).get("videoId", "")
        snippet = item.get("snippet", {})
        detail = details.get(video_id, {})
        stats = detail.get("statistics", {})
        content = detail.get("contentDetails", {})

        entry = {
            "title": snippet.get("title", "Unknown"),
            "channel": snippet.get("channelTitle", "Unknown"),
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}",
            "published": snippet.get("publishedAt", ""),
            "views": int(stats.get("viewCount", 0)) if stats.get("viewCount") else None,
            "duration": parse_duration(content.get("duration", "")),
            "description": snippet.get("description", "")[:200],
        }
        results.append(entry)

    if as_json:
        print(json.dumps({"query": query, "count": len(results), "results": results}, indent=2))
        return

    divider = "-" * 60
    for i, r in enumerate(results, 1):
        views_str = format_views(r["views"])
        date_str = format_date(r["published"])
        meta = f"{r['channel']}  |  {views_str} views  |  {r['duration']}  |  {date_str}"

        print(divider)
        print(f" {i:>2}. {r['title']}")
        print(f"     {meta}")
        print(f"     {r['url']}")
    print(divider)


if __name__ == "__main__":
    main()
