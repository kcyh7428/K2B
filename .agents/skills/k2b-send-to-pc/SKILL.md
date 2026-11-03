---
name: k2b-send-to-pc
description: Use when Keith asks to send approved text or a generated file to SJM PC, the work PC, or the SJM Inbox Edge sidebar. Publishes the selected text or one specific file from the SJM Mac. This does not send an email.
---

# Send text to SJM PC

## Send one generated file

When Keith explicitly asks to send a file, resolve the exact approved local artifact first. If more than one file is plausible, ask which one. Do not pick the newest arbitrary file, a credential file, a folder or an archive of the whole project. Do not use the text stdin publisher for file bytes.

On the configured SJM Mac, from `~/Projects/sjm-inbox`, run:

```bash
.venv/bin/python -m sjm_inbox.file_outbox publish /absolute/path/to/approved-report.pdf
```

The file must be regular, not a symlink, and no larger than 50000000 bytes. The publisher copies stable source bytes without changing the original. It replaces only the previous downloadable file; the latest text is separate. Confirm a successful exit and JSON status `published-on-mac`, ID, filename, bytes and SHA-256. On failure, report it without claiming receipt or retrying blindly. No initialization, credential changes, new HTTP calls or service restarts are part of ordinary publishing.

Say: "File published. Click Refresh, then Download file in your SJM PC sidebar." This proves publication on the Mac, not a successful Windows download. Use actual download evidence or Keith's confirmation before claiming arrival.

The file return path requires extension version 0.3.0 or newer and its reviewed receiver activation. Check the retained activation receipt and current installed module before publishing; code preparation or a ZIP alone is not activation. If it is not activated, stop and explain the missing setup rather than using another transfer service.

Codex and Kimi use the same local publisher. The Edge sidebar shows only the latest text. Publishing replaces the previous message; Keith retrieves it with Refresh and copies it into Outlook or another app himself.

## Select the text

- An explicit request such as "send this to SJM PC" authorizes publishing the clearly referenced text. Do not request the same approval again.
- Use the approved draft or explicitly selected passage. If "this" has multiple plausible targets, ask which text; do not send an arbitrary earlier message or the entire chat.
- Preserve the selected wording, Unicode and line breaks. Send plain text, not an attachment. Do not add commentary inside the payload or silently rewrite the approved content.
- The receiving key is a credential, never a message payload. This action does not authorize mailbox sending, automatic forwarding, browser control or file transfer.

## Publish on SJM

1. Confirm execution is on the SJM Mac as `keithcheung`, with the existing receiver at `/Users/keithcheung/Projects/sjm-inbox` and its `.venv/bin/python`. If running elsewhere or the publisher is unavailable, stop and explain the limitation. Do not invent a Home relay or change credentials/configuration.
2. Prepare the exact selected text privately. For a circulation draft, follow AGENTS.md: create a mode-0600 file with `mktemp` under `${TMPDIR:-/tmp}`, scan it with `rg -n '\x{2014}|--'`, review any literal exceptions, and publish only the checked bytes. Keep creation, scan, publication and cleanup in one shell/Python scope with `trap` or `finally`. Other text still follows the writing rule; never silently edit a verbatim third-party quote.
3. Run the existing publisher from its repository, redirecting the checked file to stdin. The file is a temporary input, not an attachment sent to Windows:

```bash
cd "$HOME/Projects/sjm-inbox"
"$HOME/Projects/sjm-inbox/.venv/bin/python" -m sjm_inbox.text_outbox publish < "$draft_file"
```

   Preserve the running receiver's effective `CODEXINBOX_STATE_DIR` binding. Current SJM setup uses the default private state directory. Do not introduce a different `--root`, initialize the outbox, read/print the receiving key or restart services as part of an ordinary send.
4. Check the exit status and parse the small JSON receipt. Success requires `status` equal to `published-on-mac` and a message `id`. The publisher accepts nonempty UTF-8 text up to 100000 bytes. On failure, retain the failure state and explain it; do not claim success, split the text or repeatedly retry without understanding the error. Remove the temporary file on success or failure.

## Report precisely

Say: "Published. Click Refresh in your SJM PC sidebar."

The receipt proves storage on the Mac, not receipt or copying on Windows. Confirm Windows arrival only from observed or user-confirmed evidence. The extension is manual refresh: there is no periodic polling or background delivery acknowledgement.

## Existing infrastructure

Edge version 0.2.1 uses one sign-in with the existing Codex Inbox password for sending and receiving. Manual Refresh retrieves `https://www.signhub.io/inbox/api/extension/latest`; no separate receiving key is needed in that version. The existing Signhub rewrite and Cloudflare Tunnel reach the SJM Mac receiver. Ordinary Mac publishing still uses only the local command; it does not require an HTTP call, a browser password or an AI API. Current code and setup instructions are in `~/Projects/sjm-inbox/edge-text-inbox/README.md`. This skill is installed in K2B's `.agents/skills` for new Codex and Kimi chats opened in the K2B project. Other projects and other Macs are not automatically configured.
