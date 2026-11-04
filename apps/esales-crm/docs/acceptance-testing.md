# Renewal, SMS top-up and receipt acceptance

Prepared 4 November 2026. Hosting investigation runs separately. Nothing in this checklist activates the replacement or changes the existing e-sales, Make, Pipedrive or QuickBooks services.

## Current checkpoint

The backend suite passes 96 tests and the frontend amount suite passes three. Integrated cases exercise the private authenticated operator API, staging intake key, completed renewal or top-up, immutable PDF download and delivery-worker submission to a real local SMTP receiver. Both replay the same event before and after submission and verify one receipt and one email. Additional real local SMTP cases distinguish confirmed failure, ambiguous submission, closing errors after acceptance and configurable timeout behavior for both 1TICKS purchase types. Review regressions also exercise manual entry on a staging server, atomic proof records, final callback conflicts, safe recovery, receipt voiding, authenticated action retry and PDF rendering concurrent with another payment. The browser top-up form resets to one package and displays the exact amount before submission.

A private synthetic acceptance database and two prepared PDFs are under the existing private audit folder in `crm-controlled-test-20261104`. The user-selected inbox is recorded there, outside Git. No continuous delivery worker is active for that database. Each active TEST receipt has one attempt held as uncertain after Home failed to receive SES acceptance confirmation. Neither has been retried. The public loopback demo and the private source-derived merchant import remain separate.

Keith subsequently selected the internal 1TICKS merchant for e-sales testing. Its verified live baseline and two replacement TEST receipts are recorded privately in the acceptance folder. The first two placeholder receipts are retained as void and their unsent jobs are cancelled. The current test copy uses the existing merchant identity, a test billing label and the designated test inbox. Renewal dates and prices in that copy are simulated, and no live merchant was changed. This remains e-sales workflow acceptance; the separate 1TICKS CRM migration stays later scope.

| Case | Input | Expected result | Local evidence |
| --- | --- | --- | --- |
| Renewal | 1TICKS test copy service end 22 December 2027; 12 months at MOP1,400; fixed discount MOP1,400; reported end 22 December 2028 | MOP15,400 receipt; CRM follows reported end continuing from old expiry | Passing intake/PDF/local SMTP checks; TEST receipt prepared; live expiry unchanged |
| SMS/e-token top-up | ET4000 description; quantity 1 at MOP400 | MOP400 receipt; subscription date and access status unchanged | Passing integrated HTTP, PDF and local SMTP test; TEST receipt prepared |
| Duplicate dispatch | Same reference and content before and after submission | Existing result; one receipt and one delivery, no second message | Passing in both integrated cases |
| External email | Same two stored PDFs, designated test inbox only | Correct sender, readable attachments, inbox acceptance checked separately from provider submission | Sender configured; each first attempt uncertain; acceptance and inbox delivery pending |
| Purple-arrow dispatch | Isolated e-sales test order through a separate Make scenario to staging CRM | Correct completed event reaches CRM without entering old receipt workflow | Pending isolated HTTPS endpoint and Make scenario |
| Actual renewal effect | Isolated merchant with expiry and access recorded before approval | e-sales term changes exactly once; late term continues from old expiry | External acceptance pending |
| Actual token credit | Isolated merchant with token balance recorded before approval | Purchased tokens appear exactly once; subscription unchanged | External acceptance pending |

The top-up test verifies purchased-product recording. It does not demonstrate allocation of 4,000 tokens in e-sales. No token balance is available through the recovered custom-module contract, so observe the portal or a supported read interface for that acceptance check.

## First external receipt test

1. Use a verified Converto/e-sales sender. The current app supports SMTP with STARTTLS on port 587. The AWS investigation will establish whether SES is already usable, including regional sandbox status and identity verification. Do not select a sender from another business merely because its email account is connected.
2. Review the two prepared TEST PDFs. They contain synthetic billing and issuer details and a separate TEST number series. This does not approve the final production issuer or numbering policy.
3. Configure the sender privately for the isolated acceptance database and confirm that it contains only the two synthetic orders and the designated test recipient. Keep delivery off in the ordinary demo, source-derived review database and production paths.
4. Process each delivery once through the CRM worker. Record each receipt number, message ID and provider outcome. Do not call provider submission inbox delivery. A disconnect or unknown outcome must be reconciled before retrying.
5. Check the actual inbox and attachments. If a correction is required, resend the same stored receipt with an audit reason and an explicit recipient correction; do not enter a second payment.

The user has authorized sending the two clearly marked test receipts to the designated inbox. Do not ask for that original authorization again. Sender access is now prepared in a Home-only mode-0600 file, and IAM restricts it to the designated inbox. Both first attempts are uncertain; the first inbox check reported no renewal receipt received yet. Further sends must respect those held outcomes. No Gmail draft or campaign is needed to test the CRM SMTP worker.

The top-up diagnostic recorded successful TLS, authentication, MAIL and RCPT responses, a DATA-ready response, and transfer of the MIME bytes to the local socket. The connection then timed out waiting for the final service response. This does not establish acceptance or rejection. A no-DATA probe separately verified connection/sender/recipient without submitting another message. The default socket timeout is now 60 seconds and can be configured through `CRM_SMTP_TIMEOUT_SECONDS`; that change does not replay held deliveries or prove that an earlier attempt failed.

## Separate Make and e-sales test

Read-only Make check on 4 November: scenario 891207 remains active, with its current QuickBooks and Pipedrive connections reported as available. This describes configuration, not recent execution success. No scenario was edited or run.

Its environment gate accepts production, and also staging for a particular existing merchant. Staging on that original webhook is therefore not an isolation guarantee. Never send the synthetic acceptance payloads to the original production webhook or re-send an old paid order as a test.

Prepare an independent test scenario with its own trigger, authenticated staging CRM intake and no QuickBooks or Pipedrive modules. The test CRM must have a separate database, keys and exact `CRM_INTAKE_ENVIRONMENT=staging`. A protected HTTPS endpoint is required for Make to call it; the local loopback preview cannot receive Make requests. Use real Make protected credentials rather than embedding secrets in a blueprint.

For the purple-arrow test, identify an isolated merchant/order and establish how its dispatch can reach the test trigger without routing through the old sender. This routing capability is not yet verified. If it cannot be isolated, first test Make-to-CRM using synthetic fixtures and observe e-sales effects separately. Do not silently switch the live webhook or approve a real payment merely to prove the integration.

Record the merchant's expiry, access and token balance before approval, after marking Completed and after purple-arrow dispatch. This establishes exactly when e-sales applies service or credit. The CRM records the supplied result and must not apply either a second time. Replaying to the CRM should not issue another receipt; do not infer that replaying to e-sales is safe.

The selected internal merchant supports actual event SMS through the local gateway. Treat it as an operational service, not a disposable sandbox. Test grace and suspension in the isolated CRM copy; do not shorten its live service dates or disable it to simulate overdue payment. Keep receipt delivery addressed to the designated test inbox. Capture fresh balances before any approved live top-up because normal event SMS may consume tokens during the test. Where a token ledger is available, verify the individual credit and order reference rather than infer duplicate prevention from the net balance alone. A test top-up does not require sending SMS to event customers.

## Later production gates

Production cutover additionally needs approved issuer details and receipt series, current billing/contact and service-date reconciliation, a deliberate start for new completed purchases, a verified rollback, and exactly one live receipt sender. Keith confirmed that receipts are generated for new transactions only. Historical receipt migration, old PDFs and a full prior-order ledger are not required. Do not backfill or replay older paid orders into the new sender.

Reminder SMS, grace timing, suspension and restoration are a separate acceptance group. Confirm the precise policy and test access on an isolated merchant before enabling those actions. SMS top-up purchase testing does not imply sending a reminder SMS or changing merchant access.

Keith selected nightly backups, plus a backup before deployments or bulk imports. Implement and verify that schedule at the selected host; no hourly schedule is required or activated. Server maintenance and recovery ownership remain part of the hosting decision.
