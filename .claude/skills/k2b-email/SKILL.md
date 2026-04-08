---
name: k2b-email
description: Handle Gmail operations via the gws CLI -- reading, searching, triaging, drafting, and replying to emails. Use when Keith mentions email, inbox, draft, reply, forward, triage, or references a specific email/thread. Also use when any other K2B skill needs to create a Gmail draft or read email content.
---

# K2B Email

Manage Keith's Gmail via the `gws` CLI tool. Authenticated as **keith.cheung@signhub.io**.

## Auth & Account

- **Account:** keith.cheung@signhub.io (Google Workspace)
- **CLI:** `gws` -- Google Workspace CLI
- **All raw API calls require:** `--params '{"userId": "me"}'`
- **Helpers (+ prefix) do NOT need userId** -- they handle it automatically

## Safety Rules -- Non-Negotiable

1. **NEVER send emails.** Only create drafts.
2. **NEVER delete emails.**
3. **Always confirm with Keith before creating any draft.**
4. **Use specific search criteria** -- don't pull entire inbox.
5. When Keith says "send", create a draft and tell him it's in his drafts folder.
6. When Keith says "reply", create a draft reply -- do NOT use `+reply` (that sends).

## Two Command Tiers

The gws Gmail CLI has two tiers. Use the right one for the job.

### Tier 1: Helper Commands (High-Level, Ergonomic)

Helpers start with `+`. They handle MIME encoding, threading, and formatting automatically. **But most of them SEND -- so use with extreme caution.**

| Command | What It Does | SENDS? | K2B Can Use? |
|---------|-------------|--------|--------------|
| `gws gmail +triage` | Show unread inbox summary | No (read-only) | YES |
| `gws gmail +read` | Read a message body/headers | No (read-only) | YES |
| `gws gmail +send` | Send an email | **YES** | **NO -- BLOCKED** |
| `gws gmail +reply` | Reply to a message | **YES** | **NO -- BLOCKED** |
| `gws gmail +reply-all` | Reply-all to a message | **YES** | **NO -- BLOCKED** |
| `gws gmail +forward` | Forward a message | **YES** | **NO -- BLOCKED** |
| `gws gmail +watch` | Watch for new emails (stream) | No | YES (advanced) |

### Tier 2: Raw API Commands (Low-Level)

These map directly to the Gmail API. Required for drafts since there's no helper for draft creation.

All raw commands need: `--params '{"userId": "me"}'` (plus any other params).

## Common Operations

### Triage Inbox (Read-Only)

```bash
# Default: 20 most recent unread
gws gmail +triage

# Custom count and query
gws gmail +triage --max 10 --query 'from:brunocorrea@sjmresorts.com'

# Search with Gmail query syntax
gws gmail +triage --query 'subject:The Eight newer_than:7d'
gws gmail +triage --query 'from:gerardwalker@sjmresorts.com is:unread'
gws gmail +triage --query 'has:attachment from:nelsonang@sjmresorts.com'

# Include label info
gws gmail +triage --labels
```

### Read a Specific Email

```bash
# Plain text body (auto-converts HTML)
gws gmail +read --id <MESSAGE_ID>

# With headers (From, To, Subject, Date)
gws gmail +read --id <MESSAGE_ID> --headers

# JSON format for parsing
gws gmail +read --id <MESSAGE_ID> --format json
```

### Search for Messages

```bash
# List messages matching a query (returns IDs)
gws gmail users messages list --params '{"userId": "me", "q": "subject:chef from:brunocorrea", "maxResults": 5}'

# Get full message by ID
gws gmail users messages get --params '{"userId": "me", "id": "<MESSAGE_ID>", "format": "full"}'
```

### Create a Draft

Drafts require building a MIME file and uploading it. **File must be in the working directory** (not /tmp).

```bash
# Step 1: Write the .eml file IN the project directory
cat > ~/Projects/K2B/draft_email.eml << 'EMAILEOF'
From: keith.cheung@signhub.io
To: recipient@example.com
Cc: cc@example.com
Subject: Your subject here
Content-Type: text/plain; charset="UTF-8"

Email body goes here.
EMAILEOF

# Step 2: Create the draft via upload
gws gmail users drafts create \
  --params '{"userId": "me"}' \
  --upload ~/Projects/K2B/draft_email.eml \
  --upload-content-type message/rfc822

# Step 3: Clean up the temp file
rm ~/Projects/K2B/draft_email.eml
```

**Critical notes on draft creation:**
- `--upload` path must be within the working directory -- /tmp will fail
- Always use `--upload-content-type message/rfc822`
- Always use `From: keith.cheung@signhub.io`
- Clean up the .eml file after creating the draft
- Returns a draft ID (`id` field) -- save this if you need to update/delete later

### Delete a Draft

```bash
gws gmail users drafts delete --params '{"userId": "me", "id": "<DRAFT_ID>"}'
```

### List Drafts

```bash
gws gmail users drafts list --params '{"userId": "me"}'
```

### List Threads

```bash
# Search threads
gws gmail users threads list --params '{"userId": "me", "q": "subject:The Eight", "maxResults": 5}'

# Get full thread (all messages)
gws gmail users threads get --params '{"userId": "me", "id": "<THREAD_ID>"}'
```

### Labels

```bash
# List all labels
gws gmail users labels list --params '{"userId": "me"}'
```

## Gmail Search Query Syntax (for --query / "q" param)

| Query | Meaning |
|-------|---------|
| `from:user@example.com` | From specific sender |
| `to:user@example.com` | To specific recipient |
| `subject:keyword` | Subject contains keyword |
| `newer_than:7d` | Within last 7 days |
| `older_than:30d` | Older than 30 days |
| `has:attachment` | Has attachments |
| `is:unread` | Unread messages |
| `is:starred` | Starred messages |
| `in:inbox` | In inbox |
| `in:sent` | In sent folder |
| `label:LABEL_NAME` | Has specific label |
| `filename:pdf` | Has PDF attachment |
| `{term1 term2}` | Either term (OR) |
| `"exact phrase"` | Exact phrase match |

Combine with spaces for AND: `from:bruno subject:chef newer_than:7d`

## Workflow Patterns

### "Check my email" / "What's new"
1. `gws gmail +triage` to see unread summary
2. If Keith asks about a specific one, `gws gmail +read --id <ID> --headers`

### "Draft a reply to X"
1. Find the message: `gws gmail +triage --query 'from:X'` to get message ID
2. Read the original: `gws gmail +read --id <ID> --headers`
3. Draft the response content with Keith
4. Create draft via MIME upload (see Create a Draft above)
5. Tell Keith: "Draft created in your Gmail drafts folder"

### "Forward X to Y"
1. Same as draft reply, but set the To: to the new recipient
2. Include original message content in the body

### "Search for emails about Z"
1. `gws gmail +triage --query 'Z'` for a quick scan
2. Or `gws gmail users messages list --params '{"userId": "me", "q": "Z"}'` for IDs

## Other Authenticated Services (for reference)

The gws CLI also has working auth for these services (use separate skills when needed):
- **Calendar**: `gws calendar events list --params '{"calendarId": "primary", ...}'`
- **Drive**: `gws drive files list --params '{"pageSize": 10}'`
- **Sheets**: `gws sheets spreadsheets get --params '{"spreadsheetId": "..."}'`
- **Docs**: `gws docs documents get --params '{"documentId": "..."}'`
- **Tasks**: `gws tasks tasklists list`
- **People**: `gws people people connections list`

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-email\t$(echo $RANDOM | md5sum | head -c 8)\taction: DESCRIPTION" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
