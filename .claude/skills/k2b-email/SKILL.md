---
name: k2b-email
description: Handle Gmail operations via the gws CLI -- reading, searching, triaging, drafting, and sending (only after explicit confirmation of a specific draft). Use when Keith mentions email, inbox, draft, reply, forward, send, triage, or references a specific email/thread. Also use when any other K2B skill needs to create a Gmail draft or read email content.
---

# K2B Email

Manage Keith's Gmail via the `gws` CLI tool. Authenticated as **keith.cheung@signhub.io**.

## Auth & Account

- **Account:** keith.cheung@signhub.io (Google Workspace)
- **CLI:** `gws` -- Google Workspace CLI
- **All raw API calls require:** `--params '{"userId": "me"}'`
- **Helpers (+ prefix) do NOT need userId** -- they handle it automatically

## Safety Rules -- Non-Negotiable

1. **NEVER send in the same turn you create a draft.** Sending requires a SEPARATE confirmation turn from Keith. This two-turn minimum is the core safety guarantee -- do not rationalize past it.
2. **NEVER delete emails.**
3. **Always confirm with Keith before creating any draft.**
4. **Use specific search criteria** -- don't pull entire inbox.
5. When Keith says "send an email to X": step 1 is create a draft and show him the preview + **full draft ID**; step 2 waits for his explicit confirmation of the form **`send draft <id>`** before calling `drafts send`. No other phrasing counts (see Rule 8).
6. When Keith says "reply", create a draft reply -- do NOT use `+reply` (it sends immediately, bypassing the draft step).
7. **Only one authorized send primitive exists: `gws gmail users drafts send` with an explicit draft ID that Keith just confirmed.** NEVER use `+send`, `+reply`, `+reply-all`, or `+forward` -- they skip the draft + confirmation flow.
8. **The ONLY valid confirmation vocabulary is `send draft <id>` with the exact draft ID from the preview.** Bare `send`, `send it`, `yes`, `go`, `proceed`, `ok`, `do it`, `ship it`, etc. are all INSUFFICIENT -- even if Keith clearly means the draft you just showed. The exact-ID requirement is the defense against state drift, concurrent drafts, and stale Gmail drafts colliding with the fresh one. If Keith's confirmation omits the ID, treat it as a request for clarification: reply asking him to re-send with `send draft <id>`.
9. **Ambiguity over ALL draft state, not just this conversation.** If there is more than one draft that could plausibly match a confirmation (fresh in this conversation, stale from prior sessions, or any drafts existing in Keith's Gmail Drafts folder), the ID requirement from Rule 8 must disambiguate it. Never fall back to "most recent draft" heuristics.
10. **Send-time revalidation is mandatory AND covers every outbound-impacting field.** Before calling `drafts send`, re-fetch the draft with `drafts get` and compare its current state to the preview you showed Keith across ALL of: `From` (sender identity, must equal `keith.cheung@signhub.io` -- see Rule 13), `To`, `Cc`, `Bcc`, `Reply-To`, `Subject`, **full body text** (both `text/plain` and `text/html` parts in their entirety -- NOT a truncated prefix), attachment filenames + sizes, and the draft's `message.id` (the inner message ID, which Gmail regenerates whenever the draft's message is replaced; the outer draft `id` stays stable, the inner `message.id` is the content-replacement signal). If any single field differs, abort the send, show Keith the full new preview, and require a fresh `send draft <id>` confirmation. The preview you show Keith at draft-creation time must render all of those fields explicitly (`Cc: (none)`, `Bcc: (none)`, `Attachments: (none)` if empty) -- never let a hidden field or a truncated body ride unexamined into a send authorization.
11. **TOCTOU is narrowed, not eliminated.** The `drafts get` -> compare -> `drafts send` sequence is non-atomic; the Gmail drafts API offers no if-match / etag precondition on `drafts.send`. The inner `message.id` check in Rule 10 is a content-replacement / version signal (per Gmail's documented draft semantics), not a true atomic guard -- it reduces the race window to the millisecond gap between the get response and the send call, which is acceptable for Keith's single-user setup where no other agent edits his drafts. If this skill is ever adapted to a context with concurrent draft editors, remove the send capability rather than relying on this mitigation.
12. **Voice messages never count as send confirmation.** Voice gets transcribed and could mishear; require a text confirmation.
13. **Sender identity is pinned.** The `From` header on every authorized draft MUST be exactly `keith.cheung@signhub.io`. If `drafts get` at send-time returns a `From` that differs from this (e.g. a send-as alias swap), abort the send and surface the discrepancy to Keith -- do NOT auto-approve on the grounds that the alias is technically "Keith's too". The alias list in Gmail Settings is out of this skill's scope; this skill sends as the one canonical identity only.
14. **If the body is too long to render in full in the preview, don't send via this skill.** A truncated preview is not authorization for the untruncated body (Codex round-3 finding). If Telegram's 4096-char limit or the chat UI forces truncation, reply to Keith telling him the draft is in his Gmail Drafts folder and that sending requires him to review + send from Gmail directly. The skill's `drafts send` path only authorizes drafts whose full body Keith has actually seen.

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

### Send a Confirmed Draft

**Preconditions (all required, no exceptions):**
- A draft exists with a known `id` that was previewed to Keith in a previous turn (not the current one).
- Keith's current turn contains **exactly** `send draft <id>` with the draft ID matching the preview. Bare `send` / `send it` / `yes` / any other phrasing is NOT a valid confirmation (see Safety Rule 8).
- The draft ID in his message matches the one you just previewed. If it references a different draft ID, treat it as a new request: fetch that draft, show its preview, and require a fresh confirmation of THAT id.

**Three-step send flow (don't skip step 2):**

Step 1 -- Re-fetch the draft to guard against content mutation between preview and confirmation:
```bash
gws gmail users drafts get --params '{"userId": "me", "id": "<DRAFT_ID>", "format": "full"}'
```

Step 2 -- Compare EVERY outbound-impacting field to what you previewed in the prior turn:
- `From` (extract from `payload.headers[]`; MUST be exactly `keith.cheung@signhub.io` per Rule 13)
- `To`, `Cc`, `Bcc`, `Reply-To` (extract from `payload.headers[]`)
- `Subject` header
- **Full body text** (both `text/plain` and `text/html` parts in their entirety -- not a truncated prefix; compare byte-for-byte after normalizing line endings)
- Attachment list (filenames + sizes; check `payload.parts[]` for non-text parts)
- `message.id` (the inner message ID inside the draft payload -- Gmail regenerates this whenever the draft's message is replaced, so an unchanged inner `message.id` is a strong "content not modified since preview" signal. The outer draft `id` is stable regardless of edits.)

If ANY one of these differs from what you previewed (even whitespace in a critical field, even an added Bcc, even a renamed attachment), STOP. Show Keith the FULL new preview in your reply (with all recipient fields and the new attachment list rendered) and ask for a fresh `send draft <id>` confirmation against the new content. Do NOT send on the stale approval.

Step 3 -- Only if step 2 matches exactly, call:
```bash
gws gmail users drafts send \
  --json '{"id": "<DRAFT_ID>"}' \
  --params '{"userId": "me"}'
```

**After success:** Report the returned message ID to Keith (`Sent. Message ID: <id>`).

**On failure:** Report the raw error. The draft remains in Gmail Drafts untouched -- do NOT retry automatically, and do NOT attempt any other send primitive. Let Keith decide.

**What this does NOT authorize:** creating a draft and sending it in the same turn; sending a draft referenced only from a prior session; inferring intent from context instead of the explicit `send draft <id>` vocabulary; skipping the re-fetch/compare step because "the draft probably didn't change".

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

### "Send X to Y" (two-turn flow)
1. Create the draft per "Create a Draft" above. Record the full `id` AND the inner `message.id` Gmail returned; you'll need both for Rule 10's revalidation.
2. Reply to Keith with the full preview -- EVERY field must be rendered, even if empty, and the body must appear in full (not truncated). Missing fields and truncated bodies are the attack surface:
   ```
   From: keith.cheung@signhub.io
   To: bruno@sjmresorts.com
   Cc: (none)
   Bcc: (none)
   Reply-To: (none)
   Subject: The Eight -- chef update
   Attachments: (none)
   Body:
     (full body of the draft, verbatim, no truncation)
   Draft ID: r-12345abcdef

   Reply `send draft r-12345abcdef` (copy the ID above) to send, or revise with feedback.
   ```
   If the full body would exceed Telegram's 4096-char message limit (or any other channel's limit), do NOT send a truncated preview and call it authorized. Per Rule 14, tell Keith the draft is in his Gmail Drafts folder and ask him to review + send from Gmail directly; this skill's `drafts send` path does not authorize unreviewed body tails.
3. STOP. End the turn. Do NOT send.
4. In Keith's next turn, if his message is exactly `send draft <id>` with the matching ID, run "Send a Confirmed Draft" (which itself re-fetches and re-verifies EVERY field before calling `drafts send`). Any other phrasing -- including bare `send`/`send it`/`yes`/`go` -- means ask him to re-send with the full `send draft <id>` form.

### Telegram channel specifics
When this skill runs via the Telegram bot on the Mac Mini, each Telegram message is a separate turn by definition -- the two-turn rule maps naturally to two Telegram messages. The draft preview goes back as the bot's reply with the draft ID; Keith's follow-up Telegram message must be `send draft <id>` to authorize. A single multi-paragraph Telegram message from Keith that contains both the draft request AND a `send draft <id>` line is NOT a valid confirmation -- still create the draft, show the preview in a separate reply, and wait for a subsequent message. The Telegram bot's typical reply format should surface the draft ID prominently (e.g., on its own line) so Keith can copy-paste it.

## Red Flags -- STOP and DO NOT send

If any of these match, the authorization is invalid. Refuse and ask Keith to re-issue `send draft <id>` explicitly.

- Same-turn request: "draft and send this now" in one message. Create the draft, show preview, wait for separate confirmation.
- Confirmation without the ID: `send`, `send it`, `yes`, `ok`, `go`, `go ahead`, `do it`, `ship it`, `proceed`, any emoji/reaction. These are all INSUFFICIENT -- reply asking for the full `send draft <id>` form.
- Voice/transcribed confirmation: the source was an audio/voice Telegram message, even if transcription produces the exact string `send draft <id>`.
- Confirmation ID mismatch: Keith's message says `send draft abc123` but the draft you previewed has ID `xyz789`. Do NOT auto-resolve; reply asking which draft and show the preview for the ID he typed.
- Stale draft: the confirmation references a draft from a prior session/conversation that you did not preview this turn-pair.
- Urgency pressure: "quick, send it before the meeting" -- urgency does not bypass the rule. He can copy 10 characters.
- Keith corrects the draft ("fix the subject, then send draft xyz") -- update the draft first, show the revised preview (new ID may apply), wait for a fresh `send draft <id>` confirmation against the new content.
- Re-fetch shows content mutated: step 2 of the send flow found a recipient/subject/body diff. Abort, show new preview, require fresh confirmation.

## Rationalizations (don't fall for these)

| Excuse | Reality |
|---|---|
| "Keith clearly meant send" | Intent isn't authorization. The exact `send draft <id>` vocabulary IS the authorization. |
| "Only one fresh draft exists, so bare `send` is unambiguous" | The ID requirement exists precisely because 'only one draft' assumptions break under state drift / concurrent agents / stale mailbox state. |
| "It's just a reply to a close colleague" | Recipient trust doesn't change the gate. |
| "The draft looks perfect" | Quality doesn't skip confirmation or re-fetch. |
| "He's in a hurry" | Urgency never bypasses the gate. He can copy 10 characters. |
| "Voice transcription was clear" | Voice is explicitly excluded to prevent mishear-sends. |
| "The draft won't have changed in 30 seconds" | Skipping the re-fetch is skipping the gate. Always re-fetch and compare. |
| "Keith wrote `send draft <id>` with a different ID but obviously meant the fresh one" | Different ID = different draft. Ask, don't infer. |

Violating the letter of these rules is violating the spirit of them. There is no "judgment call" exception.

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
