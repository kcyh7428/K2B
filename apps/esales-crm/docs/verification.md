# Local build verification, 4 November 2026

## Outcome

The local application records merchants and renewals, accepts completed e-sales order envelopes, independently generates branded bilingual receipt PDFs, and queues/submits them through SMTP without Pipedrive or QuickBooks. It is ready for operator review on synthetic data. No live cutover or subscription cancellation occurred.

## Evidence

- `uv run pytest -q`: 123 passed. This includes private authenticated HTTP intake through renewal or top-up PDF and local SMTP submission, replay before/after submission, immutable documents, exact money, recipient correction, backups and action claims. Sixteen 1TICKS local SMTP cases cover confirmed connection/authentication/recipient/DATA failures, unknown acceptance after disconnect, session-close errors after acceptance, and configured confirmation timeouts. Sixty-three review regressions cover staging manual entry, atomic proof confirmation, callback conflicts and provenance, merchant data, safe recovery, concurrent payment/PDF work, void reconciliation, action-slot transitions, authenticated manual action retry and explicit receipt-copy acknowledgement. Malformed login validation never echoes the submitted credential. Interrupted sender claims become visible uncertainty without replay, held payments and conflicting retries reconcile separately, and simultaneous merchant creation cannot overwrite a record. Malformed callback identities are rejected before database lookup, phone edits cancel stale queued reminders, and a recorded exception resolution cannot be overwritten.
- `npm test`: three frontend amount cases pass, including exact cents, rejection of excess precision and one ET4000 package versus the fixed renewal discount. Browser reproduced quantity staying at 12 on the old top-up form; the corrected form resets to 1 and previews MOP400.00 for a MOP400 package without recording a purchase.
- Private synthetic 1TICKS browser fixture: expired sessions now return to login; the reason-required Void control marks its test document void and opens service reconciliation; a reviewed failed-action retry queues once. The fixture has no email sender or Make connection. Its policy was disabled, queued action cancelled and temporary API stopped after verification. The original two authorized TEST email attempts remain held in a separate database.
- A separate synthetic receipt-copy browser check confirmed the required acknowledgement blocks submission while unchecked. Its delivery stayed uncertain with one synthetic attempt and zero retry events. No email service was configured; the temporary fixture API was stopped afterward.
- Actual Uvicorn proxy-header check: eight failed logins from one documentation-only address produced a ninth-attempt 429, while a separate forwarded address signed in successfully. Trusted-loopback proxy handling preserves distinct client addresses. No live credentials were used in that check.
- `npm run typecheck` and `npm run build`: pass with Node 22 / Vite 8.3.5.
- Chrome desktop on Home: loaded demo, navigated to Merchants, searched Orchard (one result), opened detail, recorded a synthetic token top-up, saw its MOP400 purchase and receipt, opened the generated PDF and visually verified Chinese text/logo/totals. Original sample subscription end stayed unchanged.
- Complete Exceptions queue: seven synthetic unmatched purchases plus one blocked email were all visible, including the oldest item beyond the overview's six-item preview. Delivery issue offered receipt controls; held orders offered review/release.
- Private source-derived database: 38 merchant records, zero orders, zero receipts. Repeat import skipped 38 existing records. No live customer email or e-sales write was invoked.
- Private database backup created outside Git. Automated restore test verifies database and original PDF bytes; sessions are invalidated on restore.
- A fresh read of Make scenario 891207 exposed the nested payment-method label path. Both integrated cases first failed with `Recorded transfer` instead of the supplied `Bank transfer`. The receiver now accepts `payment.payment_method.name` as well as the existing flat labels; the full 47-test suite passes. The live Make scenario was not changed or run.
- A separate private synthetic acceptance database contains two prepared 1TICKS TEST receipt PDFs for the user-designated inbox. Each first external SMTP attempt is held as uncertain; neither has been retried. See [acceptance steps](acceptance-testing.md).

Each replacement 1TICKS TEST receipt received one authorized external SMTP attempt through the restricted SES credentials. The top-up trace reached DATA-ready, transferred MIME bytes and then timed out before a final service response. Keith's first inbox check reported no renewal receipt received yet. Both held jobs, the exact source PDFs and private diagnostic receipts are preserved; no live merchant was changed.

## Review

Fresh reviewer found five Important defects. Each was reproduced or verified, fixed and covered by regressions. The reviewer did not issue an approving verdict on the revised code. This is not the independent Kimi approval required for any future commit. See BUILD-LOG.md for findings, fixes and explicit implementation rulings.

One deferred minor: modal focus trapping/restoration. The desktop form supports Escape, labels and visible keyboard focus; full keyboard-modal acceptance and narrow-screen visual acceptance remain pending.

Additional local operator checks passed at the phone breakpoint (354 CSS pixels observed) and normal desktop width: merchant search and detail drawer, renewal and top-up forms, removal of the renewal-date field for top-ups, invalid resend-recipient validation, and all eight exception items. The document had no horizontal overflow; wide tables remain within their own scroll areas. Normal browser sizing was restored. These checks submitted no purchases or email jobs.

Restored a backup of the private 1TICKS acceptance workspace into a new private directory. All four stored PDFs matched byte-for-byte, both active test receipt jobs stayed queued with zero attempts, the two replaced placeholders stayed cancelled, and restored sessions were empty. No worker or external delivery was started.

Independent Kimi transport attempts timed out without a verdict: the full supplied source set at 360 seconds and the narrower backend pass at 240 seconds. No fallback reviewer ran. Neither attempt constitutes an approving review.

Later Kimi runs produced dependency and application findings. Resolved the confirmed issues and recorded contextual rulings in BUILD-LOG.md. The large application reply exhausted its answer budget and ended with incomplete JSON; its findings informed repairs but it is not a usable approving verdict. Independent review of the revised source remains required. Clean Python 3.12 installation and fresh locked frontend installation/build both passed. No commit or deployment has occurred.

## Operational limits

The local preview is synthetic and unauthenticated only in explicitly marked loopback demo mode. Private mode requires operator credentials, strict-origin mutations and a separate Make key. No real data is served by the demo. SMTP and Make actions are disabled until explicitly configured.

Before receipt activation: choose hosting with persistent storage and HTTPS; configure and test the real sender; accept issuer and new receipt series; accept current merchant/billing data; review the new-transaction Make cutover and rollback; switch to exactly one live receipt sender. Keith explicitly excluded historical receipt migration. Reminders, SMS timing and access changes need separate acceptance before those actions are enabled. No QuickBooks records or existing production Make scenarios were changed. A separate GET-only Make helper retrieved historical metadata before this scope correction and is now inactive; none was imported into the CRM.

The software includes the action feed, claim/outcome contract and existing-status-module integration handoff. No backend documentation or invented e-sales endpoints are required for that bridge, but actual service/credit/access effects remain external acceptance checks. Historic source table exports are not represented as a complete activity or attachment archive.
