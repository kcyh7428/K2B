# e-sales production preparation

Prepared on 16 November 2026 from the live e-sales portal, CRM API, Make configuration and AWS console.

## Agreed outcome

Use the existing Lightsail host, SQLite, e-sales portal and Make for existing web-customer renewals, SMS top-ups and new paid receipts. The merchant reviews payment proof in e-sales. Keith approves the payment and clicks the purple arrow. CRM records the reported service end and issues one receipt per completed purchase.

There are 38 web customer candidates. CONVERTO and 1ticks are internal-use merchants. meileding is a paying mobile-app customer outside this web workflow. The separate 1ticks-test merchant remains staging acceptance data.

Service dates and access status come from the e-sales merchant management portal. QuickBooks supplies missing billing contacts. Keith's direct confirmation takes precedence for ASG's contact number. Existing receipt snapshots retain their original billing details.

## Local correction boundary

- `apps/esales-crm/server/db.py` adds an explicit web-billing flag to existing databases and preserves it when older callers omit it.
- `apps/esales-crm/server/api.py` exposes the flag through the operator API and renewal context. Excluded merchants stay visible and are excluded from renewal counts. An authenticated operator endpoint records specifically reconciled top-ups with existing external receipts.
- `apps/esales-crm/server/orders.py` holds purchases outside web billing and creates an access-review task for a paid renewal whose recorded access is grace or inactive.
- `apps/esales-crm/server/automation.py` plans reminder actions only one day before service end and on the service end day. Claims and retries enforce each action's original scheduled day and render the current approved SMS template. Queued-action cancellation writes an activity record. A stored policy with another lead day requires fresh approval of the one-day schedule. Queued obsolete suspension actions are cancelled and cannot be claimed or retried. Processing access outcomes are held for manual reconciliation without changing merchant access.
- `apps/esales-crm/web/src/main.tsx` exposes the web-billing flag and removes the suspension timing control. The old grace_days policy field remains accepted for stored-config compatibility and has no scheduling effect.
- Focused tests cover a pre-change database, an excluded merchant, a late renewal, exact reminder dates including claims and retries, obsolete suspension actions, old API callers, and existing-receipt top-up recording and replay. Differing submitted receipt evidence produces a visible conflict instead of silently replacing provenance.

## Recent top-up reconciliation

The newest 100 e-sales orders were inspected, covering November 2026 back through October 2025. Three completed November SMS top-ups are absent from the CRM. Each is ET4000, 4000 tokens, MOP400. Blush order shfotz on 16 November is linked to QuickBooks receipt 2801 with EmailSent status. Keith confirmed that Barbara Macau order byapr6 on 11 November and Hedonic order nyqwls on 6 November were already receipted elsewhere. Their external receipt numbers remain unknown.

Prepare these three orders through the existing-receipt recording path, preserving original purchase dates and receipt provenance. Generate no receipt or email and make no e-sales token-credit or service-date changes. Retain the normalized original order identity so an identical later webhook cannot issue a duplicate receipt. Blush has the retained Make source payload. Barbara and Hedonic are reconstructed from observed portal fields and remain labelled as such. A differing replay is held for reconciliation.

The private preparation manifest is retained outside Git under Codex Audits as permanent reconciliation evidence, including through production cutover and subscription cancellation. It has no automatic retirement step. Write each retained-Make-payload or reconstructed-from-portal label into the operator import reason, which is retained in the order's payment.manual_confirmation audit event and database backups. Prepared entries are not live imports until the reviewed endpoint is installed and a verified live import is recorded. Carry these three external-receipt markers into the production database with production order identities before accepting customer events.

## Acceptance

- [x] Existing merchant data loads with web billing enabled by default in local regression tests.
- [x] An excluded merchant produces no receipt, delivery or reminder action in local regression tests.
- [x] A paid renewal for a grace-period merchant records its expiry and receipt and creates one manual access-review task locally.
- [x] Replaying the same paid order preserves one receipt and one access-review task locally.
- [x] Advance and expiry reminders follow the service end date locally. Overdue dates do not generate catch-up messages or suspension actions.
- [x] Existing queued suspension actions cannot be claimed or requeued locally.
- [x] Disabling web billing cancels queued reminders and is preserved by an older caller that omits the new field locally.
- [x] Backend verification passed with 174 full-suite tests, including malformed-policy visibility and per-action cancellation records. Frontend typecheck and build passed with the existing project-scoped Node 22 runtime.
- [x] Existing-receipt recording creates no receipt or email and an identical webhook replay cannot issue a duplicate in local regression tests.
- [ ] Before importing the three top-ups into staging, take a fresh database/PDF backup and verify an isolated restore. Keep sending disabled and the mail worker stopped. Then import through the reviewed endpoint and verify that receipt, delivery and service-date values are unchanged. A production import needs its own fresh backup and isolated restore first.
- [ ] An independent Kimi review approves the exact delivery diff before any commit.

## Verified infrastructure and external dependencies

SES production access was approved on 16 November 2026 at 12.26 Macau time, case 179480316200037. A live AWS read confirmed production access and sending enabled, with a 50000-per-day quota and a 14-per-second limit. The e-sales.app sender domain is verified and DKIM status is SUCCESS. Account-level BOUNCE and COMPLAINT suppression is configured.

These are account-level SES capabilities. The dedicated production SMTP identity has not been installed and the replacement customer-mail path has not been activated. Sending stays disabled during this staging deployment and import.

The existing SMTP IAM user still permits only receipts@e-sales.app to keith.cheung@signhub.io. A separate production mail identity and its exact permissions require review before installation. The staging mail identity remains the acceptance sender.

The Lightsail instance is running. The latest S3 backup observed was daily/staging/20261115T190138.zip, uploaded at 03.01 Macau time on 16 November. A fresh backup and isolated restore are required for the production dataset before switching.

## Production configuration and Make work

- [ ] Prepare a separate production database, data directory, operator access and intake credential on the existing host.
- [ ] Verify the deployment interpreter uses SQLite 3.35 or newer for cancellation-audit RETURNING statements before enabling reminder processing.
- [ ] Copy the accepted merchant records and billing corrections. Set web billing false for CONVERTO, 1ticks and meileding. Keep test-copy orders and documents in staging.
- [ ] Agree the production receipt series before its first issue. Proposed series is ESALES-2026- starting at 00001. The staging series is already locked at TEST-ESALES-2026- with eight issued records.
- [ ] Prepare the production receipt sender and validate SES submission, inbox arrival and its PDF with an approved recipient.
- [ ] Preserve the exact existing Chinese MessageBird texts privately. The approved schedule is noon Macau, one day before service end and on service end.
- [ ] Replace scenario 891207's Pipedrive/QuickBooks receipt paths with authenticated production CRM intake, preserving the existing webhook entry point and original order reference.
- [ ] Replace scenario 2092924 with the claimed CRM reminder feed. Keep the authenticated claim and outcome reporting so a replay cannot send the same reminder again.
- [ ] Retire scenario 2099266's Pipedrive dependency. Manual access changes remain an e-sales operator step in this release. The historical Lost-to-inactive path is distinct from unpaid-expiry enforcement.
- [ ] Retire unused new-client scenarios 2125814 and 3071971 at the agreed switch. New subscriptions are outside this project scope.
- [ ] Reconcile the stored failed 1TICKS test run in 891207 separately from customer transactions. Do not replay its receipt blindly.

## Switch acceptance and cancellation

Record the cutover time and save the current scenario configuration. Switch to one receipt path, then one reminder path. Verify a controlled renewal, a top-up, duplicate dispatch, correct merchant dates, one receipt each, one email each, and suppression of reminders after renewal. Verify the manual access-review task on a grace-period renewal.

For rollback, stop replacement intake and sending, preserve its issued references and queue states, and reconcile external effects before returning unprocessed purchases to the old path. Keep the last accepted application release and the before-data snapshots.

Export current Pipedrive records and retain QuickBooks records before cancellation. A CSV preserves structured deal fields and does not establish complete attachment, email or activity history. Historical receipts are archived separately from the new-transaction CRM.

Before cancellation, verify every relevant Make connection and shared workflow has a replacement or is deliberately retired. The separate phase-two accounting workspace has its own cancellation decision. Cancellation is ready only after the production workflows are accepted and the subscriptions' remaining dependencies are resolved.

## Delivery state

This file prepares the production work. Local code verification, independent review, commit, push, staging deployment, production installation, workflow activation and subscription cancellation are separate recorded states. Stop on a review rejection, missing production credential, unexpected recipient, concurrent configuration change or unresolved payment effect.
