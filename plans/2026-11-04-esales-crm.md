# e-sales CRM and Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one private operator application replacing Pipedrive records and QuickBooks paid receipt generation for existing e-sales merchants.

**Architecture:** A FastAPI service owns SQLite transactions, immutable receipt snapshots, PDF files and a durable SMTP delivery queue. A TypeScript React interface is served by the same origin. Make posts completed e-sales events through a separate authenticated intake; e-sales keeps payment approval, service enforcement and token metering.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLite, ReportLab with embedded Traditional Chinese font, SMTP; React, TypeScript, Vite, Node 22.12+.

**Spec:** `/Users/keithmbpm2/Library/Application Support/Codex Audits/e-sales-2026-11-04/crm-and-receipts-design.md`, approved by Keith 4 November 2026. Source exports remain private outside Git.

## Global Constraints

- Replace both Pipedrive and QuickBooks in the first usable release. Keep Make and e-sales.
- No new subscriptions, new-client provisioning or 1TICKS migration.
- Preserve manual payment proof approval and Completed then purple arrow dispatch.
- Renewal reminders follow e-sales service end date. Late renewal continues from the old expiry date.
- No double application of tokens/service periods to e-sales. Ingest reported service end.
- No automatic bank approval, live emails, live Make edits, access changes, subscription cancellation, commit, push or deployment in this build.
- Operator authentication, intake authentication and private receipts required. Never put live customer exports, credentials or database files in Git.
- Exact monetary arithmetic, unique order identity, durable delivery state and retry without a second receipt.
- Exact reminder offsets and suspension policy remain disabled pending confirmation.
- Keith clarified that receipt generation starts with new transactions at cutover. Historical receipts, PDFs and complete prior-order migration are excluded. Retain current merchant/billing details and service dates, deduplicate new order references, and do not backfill or replay older paid orders. The unused historical receipt importer was removed from this release to keep that scope explicit.

## Review Focus

1. Same reference with changed content must be held without altering an issued receipt.
2. Token purchases must not clear subscription grace or alter expiry.
3. Concurrent events/workers must not duplicate receipts or delivery jobs.
4. Long Traditional Chinese lines and multiple pages must remain readable with embedded fonts.
5. Malformed states, amounts, addresses and unauthorized cross-origin requests must fail without financial side effects.

## Files

All product files are under `apps/esales-crm/`. `server/config.py` holds environment configuration; `db.py` schema/transactions; `orders.py` validation and event intake; `documents.py` immutable PDFs; `delivery.py` queue and SMTP; `api.py` authenticated operator routes; `cli.py` local administration/import/backup; `migration.py` audited read-only source conversion. `web/src/` contains the UI. `tests/` tests actual service and database behavior using temporary stores and synthetic customers.

### Task 1: Durable order and merchant records

**Interfaces:** `Store(path)` initializes the schema; `ingest(store, payload, business, environment)` returns `{order_id, state, duplicate}`. `normalize(payload)` returns validated exact minor-unit lines and a financial fingerprint.
- [ ] Write tests for completed monthly renewal, 5th Plus age independence, six-month term, exact fixed discount, top-up, duplicate, conflicting duplicate, unknown merchant, mixed items, invalid money and decreasing/missing expiry.
- [ ] Run `uv run pytest tests/test_orders.py -q`; expect missing implementation first.
- [ ] Implement atomic order/event/receipt/job reservation with case-insensitive unique merchant codes, review holds and immutable snapshots. Use reported expiry, never payment-day arithmetic.
- [ ] Run the focused tests and append results to `BUILD-LOG.md`.

### Task 2: Documents and delivery

**Interfaces:** `render_receipt(snapshot, output, font_path)` creates a PDF; `process_delivery(store, settings)` processes one queued job with explicit submitted/failed/uncertain states.
- [ ] Write tests for PDF contents/embedded font, multipage text, SMTP submission and PDF attachment, missing sender configuration, retry preserving identity, and concurrent claim protection.
- [ ] Observe failures, implement ReportLab rendering and SMTP with timeouts, and verify tests.
- [ ] Require issuer configuration and receipt-number prefix. Default local receipts use a distinct test series; no silent continuation from 2798.

### Task 3: Private operator API

**Interfaces:** `create_app(settings)` provides `/api/session`, `/api/overview`, `/api/merchants`, `/api/orders`, `/api/receipts/{id}/pdf`, `/api/exceptions`, `/api/events`, `/api/settings` and `/hooks/esales`.
- [ ] Test authentication boundaries, login/logout/session expiry, origin protection on mutations, webhook-only credentials, bounded payloads, correction audit reasons, held-order release and immutable issued receipts.
- [ ] Implement cookie sessions with server-side hashed tokens, scrypt operator password hash, CSRF-origin checks and security headers. Reject absent configuration outside demo mode.
- [ ] Add JSON export, verified backup/restore CLI, health/readiness and manual retry endpoints. No credentials returned to the UI.

### Task 4: Operator interface

**Interfaces:** React fetches the same-origin JSON API with session cookies. Dashboard, merchant search/detail/edit, purchases, receipt preview/retry, exceptions, settings and event history share typed models.
- [ ] Add browser-verifiable scenarios for login, search/detail, issuing a synthetic manual approved purchase, viewing/downloading a receipt and inspecting retry failure.
- [ ] Build a responsive warm-paper/coral interface with clear status, keyboard access, loading, empty and failure states. No live customer data bundled into assets.
- [ ] Run `npm run typecheck` and `npm run build`; inspect the running app in the browser.

### Task 5: Migration and Make handoff

**Interfaces:** `python -m server.cli import-audit PATH` imports the approved candidate set from private snapshots without sending anything. `backup DEST` and `restore ARCHIVE` preserve database and documents with checksums.
- [ ] Test import twice, unresolved match refusal, observed expiry authority, current billing data, no historic email replay and backup/restore with an issued PDF.
- [ ] Import 38 matched candidates only into a private local review database after preserving source snapshots; leave additional e-sales codes for review.
- [ ] Write exact proposed Make HTTP handoff, authentication, synthetic payload, cutover, rollback and operational steps in README and `docs/make-handoff.md`. Do not edit live Make.

### Task 6: Integrated verification and review

- [ ] Run all backend tests, typecheck and frontend build.
- [ ] Exercise real local HTTP -> SQLite -> PDF -> test SMTP using synthetic data; inspect rendered PDF and browser layouts.
- [ ] Obtain a fresh whole-change review, fix important findings with regression tests and rerun checks.
- [ ] Record precise local build state, configuration still needed, policy gates and no-live-cutover status. Keep an uncommitted checkpoint; do not delete its progress evidence.

## Execution decisions

Keith explicitly authorized the plan and build in one request, so proceed inline without a second execution-method approval. Native Codex worktree isolates unrelated dirty K2B work. AGENTS forbids unsolicited commits, so all task checkpoints stay uncommitted and the ledger replaces commit-based task helpers. Local SQLite is a deliberate small single-operator choice; production requires a persistent disk, HTTPS, backups and an authenticated email sender before activation. Python PDF tooling keeps bilingual receipts independent of QuickBooks. No new paid AI dependency.

Subsequent authorizations: Keith selected the existing internal 1TICKS merchant and keith.cheung@signhub.io for two clearly marked synthetic receipt emails. Each first SMTP attempt is uncertain and held; no retry is implied. In the dedicated AWS thread Keith explicitly authorized the main CRM task to continue staging deployment. That opens the reviewed Git delivery and AWS staging path through k2b-ship, superseding the initial build-only delivery limit for staging. Production Make cutover, actual merchant changes and subscription cancellation remain separate. The current target is one AWS Ubuntu host, not activation on another Mac.
