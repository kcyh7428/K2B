# AWS staging installation and acceptance

This release covers new e-sales renewals, SMS/e-token top-ups and their payment receipts. Use the private 1TICKS test workspace and the designated test inbox. Historical receipt migration is excluded. Keith authorized staging deployment in the AWS thread; production cutover is a later operation.

## Release input

Use an independently approved Git commit on the CRM branch. Record the exact reviewed paths and verify that their hashes match the staged and committed source. Build frontend assets from that commit using Node 22.12 or newer and the checked-in package lock. Export production Python requirements from its uv.lock with uv export, frozen lockfile, development dependencies excluded and project emission disabled. Retain dependency hashes.

The runtime bundle contains server/, assets/, deploy/, web/dist/, the production requirements, pyproject.toml, uv.lock and a manifest binding the source commit and every bundled file's SHA-256. Validate paths before extraction into a new release directory. Exclude virtual environments, node_modules, tests, private configuration, databases, PDFs and audit exports. The existing Converto logo and licensed Chinese font are runtime assets, not customer data.

Keep the previous installed release until staging acceptance succeeds. Do not overwrite a running release directory or use an unreviewed working copy as the release source. Build and verify the new virtual environment against its exported locked requirements before switching the staging current link.

## Private installation

The current infrastructure handoff lives in the private audit directory under aws-infra-preparation/infrastructure-handoff.md. Consult it for the actual host, public address and verified connection state. It reserves these server locations:

- /opt/esales-crm/staging/current: application working directory and virtual environment.
- /var/lib/esales-crm/staging: database and immutable receipt PDFs, owned by esales-crm and mode 0700.
- /var/backups/esales-crm/staging: private local backup archives.
- /etc/esales-crm: root-only configuration directory, mode 0700.

Use a verified secure transfer route. Do not paste credentials into browser SSH, weaken SSH host-key checks or TLS validation, put secrets in a release bundle, or install administrator AWS credentials on the application host. Install only the dedicated test SMTP and backup identities. Configuration files remain mode 0600 and are read by the service manager.

Set CRM_DATA_DIR to the staging data directory, CRM_PUBLIC_URL to the exact HTTPS origin and CRM_INTAKE_ENVIRONMENT to staging. Configure an operator password hash and a separate random intake key. Keep CRM_DELIVERY_ENABLED false initially. SMTP uses STARTTLS on 587; CRM_SMTP_TIMEOUT_SECONDS defaults to 60. The test mail identity is restricted by IAM to the designated inbox and is not a production customer-mail credential.

Confirm the staging database is the test workspace, not the private import of current paying merchants. Its two replacement TEST receipts currently have uncertain first attempts. Preserve those states and their original PDFs. Neither restoring a backup nor changing the SMTP timeout authorizes resending them. Keep the worker stopped until that queue is inspected and the held outcomes are resolved deliberately.

## API and backup checks

1. Install the reviewed deploy/ API/worker templates and verify all application/backup units against the actual interpreter. Start only the loopback API. The API template explicitly trusts forwarded addresses only from 127.0.0.1, and the Caddy template replaces X-Forwarded-For with its actual remote peer. Leave the worker and nightly timer stopped.
2. Check local health with the public Host header: curl -H 'Host: crm.e-sales.app' http://127.0.0.1:8765/health. Every loopback request must use that Host header; authenticated readiness also needs an operator session. Check private authentication, same-origin mutations, staging-only intake, merchant search, both purchase types, private PDFs, duplicate references and visible delivery exceptions. Use synthetic 1TICKS data and local capture for pre-mail checks.
3. Create a consistent database/PDF archive with the app backup command. Upload through the dedicated backup identity, download the object and compare its SHA-256.
4. Restore into a new private directory and verify SQLite integrity, all stored PDF hashes, receipt identities, delivery states and invalidated old sessions. Do not point the restored copy at a sending worker.
5. Enable the existing nightly timer only after its actual upload/download/restore checks pass. Its selected schedule is nightly at 03:00 Macau time, plus a separate backup before deployments or bulk imports. Verify the actual OnCalendar includes the Asia/Shanghai timezone suffix, as prepared, and read back the next trigger as 19:00 UTC (03:00 Macau the following day). The host's default timezone must not change this schedule.
6. Validate the reviewed deploy/Caddyfile on the host, then switch the setup placeholder to it. Its /healthz route proxies the actual app /health. Verify HTTPS, login, security headers and private receipts through the public address. Verify spoofed/absent forwarding headers cannot change the client's login bucket. Keep automatic sending and merchant actions off until their acceptance steps pass.

## Make and receipt acceptance

Use a separate Make staging scenario and its own authenticated connection to this staging intake. Send the prepared 1TICKS renewal and top-up envelopes, then replay the same references to confirm one receipt per purchase. Check exact currency/amount, fixed discount, reported renewal date and unchanged subscription date for the top-up.

The existing production scenario also accepts one staging merchant. Do not use its webhook for these acceptance fixtures. The real purple-arrow route for the internal merchant must be isolated explicitly before exercising it. The app records e-sales results; it does not credit tokens or extend live service again.

After resolving the currently held test email attempts, use the same stored receipt documents for a deliberately approved retry if needed. Record SMTP submission separately from inbox receipt. Provider callbacks require an authenticated adapter; until it exists, do not label SMTP submission delivered.

The internal 1TICKS merchant powers operational event SMS. Keep its real access enabled and service dates intact. Validate any eventual actual token credit with a fresh baseline and individual transaction evidence, allowing for simultaneous event SMS consumption. No event-customer SMS is needed for receipt acceptance.

## Staging rollback

Stop the worker and new intake before any rollback. Preserve the current database, PDFs, event log and delivery states. Reconcile post-backup orders and uncertain external effects before considering a restored database. A restore cannot unsend email. Keep the test identity and staging key scoped separately from future production.

Restore the last accepted release and private data deliberately, run the same health/authentication checks and keep sending disabled until queue reconciliation completes. Changing the application link alone is not a data rollback. Existing merchant, Pipedrive and QuickBooks production workflows stay under their original sender until their separate cutover is accepted.
