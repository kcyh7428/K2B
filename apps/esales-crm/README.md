# e-sales operations

A private single-operator CRM for current e-sales merchants, renewals, token purchases and independently generated paid receipts. No runtime dependency on Pipedrive or QuickBooks.

## Local preview

Python 3.12+ and Node 22.12+ are required. The checked-in lockfiles fix dependencies. The current Home default Node 20.9 is too old for Vite; a project-scoped Node 22 package was used without changing the system runtime.

```sh
cd apps/esales-crm
uv sync
cd web
npm ci
npm run build
cd ..
export CRM_DEMO=1
export CRM_DATA_DIR=/private/tmp/esales-demo
uv run python -m server.cli demo
uv run uvicorn server.api:create_app --factory --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. Demo contains only synthetic data and opens only to loopback clients. It cannot open a database marked private. Real imports refuse demo mode. Email sending and automation are off by default. The operator can record a synthetic completed order, open its PDF, inspect blocked email and retry it.

## Private installation configuration

Do not activate until the receipt issuer/series, billing contacts, sending identity, new-transaction cutover and e-sales behavior are accepted. Reminder/access policy can remain disabled independently. A persistent single-host disk is required for SQLite and receipts. This is not suitable for an ephemeral/serverless filesystem or multiple independent replicas. Terminate HTTPS at the chosen host; set `CRM_PUBLIC_URL` to its exact HTTPS origin.

Set these server-side environment variables, never in frontend assets or Git:

- `CRM_DATA_DIR`: private durable folder, separate from demo.
- `CRM_PUBLIC_URL`: exact origin, such as `https://crm.your-domain.example`.
- `CRM_PASSWORD_HASH`: scrypt encoded hash. Generate with `uv run python -m server.cli password-hash /private/path/operator-hash`. Password entry is local and hidden; the hash is saved to a mode-0600 file and is not printed.
- `CRM_INTAKE_SECRET`: separate random secret of at least 32 characters for Make. Keep it server-side and in Make's protected connection/header fields.
- `CRM_INTAKE_ENVIRONMENT`: `production` by default. Use a separate database and key for staging.
- `CRM_SMTP_HOST`, `CRM_SMTP_PORT` (587 default), `CRM_SMTP_TLS` (true), `CRM_SMTP_USER`, `CRM_SMTP_PASSWORD`, `CRM_SMTP_FROM`.
- `CRM_SMTP_TIMEOUT_SECONDS`: socket timeout in seconds, 60 by default. Slow confirmation remains uncertain if the service disconnects or does not acknowledge the message; a larger timeout does not authorize retrying an uncertain attempt.
- `CRM_DELIVERY_ENABLED`: remains false until a controlled email test is accepted. Remote SMTP requires TLS. Port 465 implicit TLS is not implemented; use STARTTLS on 587 with your provider.
- `CRM_EMAIL_HOOK_SECRET`: optional independent key for a provider/Make delivery callback adapter. Native provider callbacks need an adapter to this app's contract.

The API refuses non-demo startup without operator and intake credentials. Sessions last 12 hours; cookies are HttpOnly/SameSite strict and Secure on HTTPS. Mutations require the configured Origin; webhook Bearer credentials cannot read operator APIs. Login is rate limited. There is no public receipt URL. The current static UI and JSON API share one origin.

Issuer settings are configured in the operator UI. Receipt numbering uses an explicit distinct series rather than guessing that the next old QuickBooks number is 2799. The series locks after first issue. Issued billing, issuer, amounts and descriptions are immutable snapshots. Archived receipts do not regenerate from current merchant data.

## Import and backup

The private audit folder is not included in this repo. `import-audit` reads the captured candidate checks, e-sales observed service end dates and QuickBooks billing table. It imports 38 matched current candidates once, removes a grace title prefix, and uses observed e-sales service end for reminder deadlines. It creates no orders, receipts or emails. Repeat runs skip existing records instead of overwriting corrections. The additional five active/grace e-sales codes require separate inclusion decisions.

```sh
uv run python -m server.cli import-audit /private/path/to/audit
uv run python -m server.cli backup /private/path/new-backup.zip
uv run python -m server.cli restore /private/path/new-backup.zip --destination /private/path/new-restored-folder
```

Backups contain the SQLite snapshot, referenced PDFs and a SHA-256 manifest. Restore validates paths, checksums and SQLite integrity into a NEW directory, adjusts document paths and invalidates old sessions. It never overwrites existing data. Backups are private but not encrypted; store them on protected storage. Do not delete the original historical exports or receipts after importing a table snapshot.

The selected hosting policy is one nightly backup, plus a backup before deployments or bulk imports. Schedule and restore acceptance belong to the chosen host setup; no backup scheduler has been activated.

Keith confirmed the receipt scope is new transactions only. Import current merchant/billing details and service dates, then start receiving new completed purchases at the deliberate Make cutover. Do not backfill or replay older paid orders into this fresh receipt series. The CRM tracks each new order reference to prevent duplicate receipts on transport retries. Old receipt history and original PDFs are not a migration requirement or activation gate.

Historical receipt ledgers and original PDF imports are outside this release. The remaining import-audit command prepares current merchant and billing records only; it does not import old purchases. No historical receipts exist in the review, demo or controlled-test databases.

## Delivery worker

Run `uv run python -m server.cli worker` as a supervised process on the same persistent host. `--once` processes one item. Receipt PDF rendering is serialized and stored once. SMTP submission is not proof of inbox delivery. Retrying uses the same receipt/PDF with a separately tracked message ID per attempt. Explicit recipient corrections are audited without changing the issued document. Old-attempt callbacks require a retained attempt record and do not change a newer attempt's outcome. A confirmed connection/authentication failure or explicit SMTP rejection is failed and can be corrected and retried. A timeout/disconnect during submission can mean the server accepted a message without confirming; these jobs enter `uncertain`. Retrying an uncertain or submitted attempt requires a reconciliation reason beginning `RECONCILED:` and nonempty evidence. Sending another copy after an uncertain, submitted or delivered attempt also requires the explicit `allow_duplicate_copy` acknowledgement, recorded in the audit. An error closing a session after the message was accepted does not undo its submitted state.

Each delivery pass acquires the exclusive host sender lock before inspecting jobs. Interrupted `processing` jobs found while holding that lock become `uncertain` with a visible delivery exception; they are never automatically resent. The operator can also stop all workers and run `recover-delivery`. Do not run manual recovery while another worker is sending. Never automatically retry an uncertain email or Make action. `/hooks/email` accepts authenticated `{message_id,status}` with `delivered` or `bounced`, including a provider-confirmed final outcome for an uncertain current attempt; without an adapter, only SMTP submission is tracked. Manual delivery failures remain visible in the exceptions view.

Releasing a held payment clears its payment-review issue only. A conflicting retry remains a separate exception until the operator reconciles it explicitly. Reconciling that conflict does not release the original held payment or issue a receipt.

Sending and interrupted-delivery recovery share an exclusive host lock. Recovery refuses while a sender is active. Final per-attempt callback states do not overwrite each other; conflicting delivery/bounce reports create a reconciliation issue. The operator's process-next endpoint waits for SMTP and may take a minute or longer across several protocol steps. Wait for its result and inspect the saved status after a connection interruption. Normal unattended sending uses the supervised worker.

Enabling delivery does not automatically requeue blocked receipts. Review their exception and original PDF, then use the receipt's Resend control to queue that document deliberately. Keep the worker stopped during initial sender setup to avoid creating blocked attempts. Unknown outcomes require actual reconciliation detail after RECONCILED:, never the prefix alone.

Voiding a receipt is a document correction, not a payment reversal or an e-sales service cancellation. Uncertain, submitted or processing email outcomes must be reconciled first. Voiding a renewal creates a service-reconciliation issue: verify the actual e-sales term and payment, then correct the CRM date if needed. Do not automatically roll back an observed service term just because its document was voided. Callbacks for void documents are retained as history only.

The Receipts view offers Void receipt for eligible documents and requires a reason. It then opens Exceptions when service or recipient reconciliation is required. A second void is refused. Unsupported historical records are reported explicitly; this release does not import them.

Make claims older than five minutes are held as uncertain by the next planning call or `recover-actions`; they are never automatically sent again. Pause the Make consumer, inspect its execution and verify actual SMS/access effects before resolving the issue. A repeated claim remains rejected to prevent duplicate external effects after an ambiguous response. Timing and access policy remain disabled until separately accepted.

Settings lists reminder/access actions. Review retry permits a deliberate requeue of a failed, uncertain or cancelled action only while policy is approved/enabled and its merchant term remains current. An uncertain retry requires confirmation and evidence that the earlier external effect was not applied. The intake key cannot perform this operator action. Submitted actions cannot be requeued.

The AWS sender unit uses Restart=on-failure and RestartSec=5. Unexpected database transaction errors stop that process for the supervisor to report/restart, rather than hiding a storage failure and continuing to send. Persistent restart failure needs operator intervention. After any interrupted send, stop workers and reconcile the held outcome with recover-delivery before retrying.

## Make integration and access actions

See [the exact handoff](docs/make-handoff.md). Build and test separate proposed scenarios before replacing the live branches. Current Make entry point and e-sales approval can remain. Remove all Pipedrive lookup/update and QuickBooks creation/send modules from the replacement paths. CRM order intake stores the reported expiry; it does not apply service or tokens to e-sales a second time.

Reminder/access actions are disabled by default. The proposed two-day lead and three-day grace settings are visible, editable and require explicit policy approval. The feed is planned from e-sales service end, not the old Pipedrive due date. Make claims each action just before acting, sends MessageBird SMS or calls the existing custom e-sales status module, then reports its outcome. Suspension success requires a separate getMerchant status readback. A renewal cancels queued obsolete actions; a renewal during an in-flight action creates an access verification issue.

## Verification

```sh
uv run pytest -q
cd web
npm run typecheck
npm run build
```

Tests use temporary databases, synthetic records, real PDF rendering and a local SMTP listener. No test reaches real customers or e-sales. UI and PDF are also inspected locally. The font is Noto Sans TC from [google/fonts](https://github.com/google/fonts/tree/main/ofl/notosanstc), embedded under the included OFL license. The Converto logo was extracted unchanged from the existing receipt with user-authorized account access. No customer billing block is shipped.

See [controlled workflow acceptance](docs/acceptance-testing.md) for what is already tested locally, what the external test emails require, and how to isolate the Make and e-sales tests from the current accounting workflow.

The [AWS staging runbook](docs/operations.md) covers the reviewed release, private installation, backup/restore checks and controlled receipt acceptance.

Reference implementation documentation: [FastAPI authentication](https://fastapi.tiangolo.com/advanced/security/http-basic-auth/), [ReportLab fonts](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/), [Vite runtime requirements](https://vite.dev/guide/).

## State of delivery

This is a local build and review checkpoint. Live Make workflows, SMTP sender configuration, deployment, subscription cancellation and actual access-policy activation are separate operations. A receipt cutover requires accepted current merchant data, a controlled test receipt email, a deliberate start for new completed purchases, one receipt sender and a verified rollback. Reminder/access actions require their own acceptance before activation. No commit, push, merge or production deployment is implied.
