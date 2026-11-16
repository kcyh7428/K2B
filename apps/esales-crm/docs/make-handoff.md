# Make replacement handoff

Prepared configuration only. No live scenario changed.

## Completed payment path

Preserve the existing e-sales webhook entry point in a reviewed replacement of scenario 891207. Keep its production gate and pass the original env/data envelope to an HTTP POST at `CRM_PUBLIC_URL/hooks/esales`. Send `Authorization: Bearer <private intake key>` and `Content-Type: application/json`. Use Make's protected credential/header configuration; never paste live keys into chat, source or this document.

The receiver requires `env=production` by default and `data.status=completed`. Required fields: `code`, `merchant_code`, `currency` (MOP or HKD), `items` with `system_product_type`, `system_product_name`, `quantity`, `unit_price`, and `payment_price`. Optional `price` must equal the line subtotal, `coupon_discount` is a fixed currency amount, `coupon_code` is recorded. Monthly renewals require `merchant_subscription_end_date`. The existing Make scenario reads the payment label from `payment.payment_method.name`; the receiver now preserves that path, with compatibility for flat `payment.name` and `payment.method` labels. Unknown/mixed items, absent billing match, decreasing expiry or unapproved issuer produce a held order with an exception and no receipt.

Example synthetic payload:

```json
{"env":"production","data":{"code":"SYNTHETIC-001","merchant_code":"SAMPLE01","status":"completed","currency":"MOP","items_type":"monthly","items":[{"system_product_type":"monthly","system_product_name":"系統月費","quantity":12,"unit_price":1200}],"price":14400,"coupon_discount":0,"payment_price":14400,"merchant_subscription_end_date":"2027-10-22","payment":{"payment_method":{"name":"Bank transfer"}}}}
```

A 200 response contains `order_id`, `state` and `duplicate`. `accepted` reserves one immutable receipt and a delivery job; the worker renders/sends it. `review` and `conflict` are visible operator tasks, not successful receipt delivery. A repeated identical purchase returns its existing result without another email job. A changed amount with the same reference is held, not overwritten. Do not retry 422 validation errors indefinitely; alert for review. Retry transport failures using the SAME original order reference and payload.

Before activation verify exact current e-sales total/discount semantics against more than the synthetic fixture. Keith selected new transactions only: establish the cutover, review pending events and route new completed purchases to the CRM. Do not backfill or replay older paid orders. The CRM deduplicates the new order references it receives. Importing historical receipts or PDFs is not required for this scope.

## Daily reminders

After timing and message behavior is confirmed, enable the operator policy. Preserve the verified noon Macau schedule, one day before service end and on the end date. Make POSTs `/hooks/actions/plan` with the intake Bearer key. The response contains reminders due on the exact configured advance or expiry day. Overdue dates do not create catch-up or suspension actions. For each item:

Advance and expiry are two different intended messages for a service period. Each has its own single-use identity. An uncertain advance outcome is not replayed automatically and does not suppress the separate expiry message. A paid renewal cancels obsolete queued reminders.

1. POST `/hooks/actions/{id}/claim` with the same key. Only a successful claim authorizes proceeding. Renewed/cancelled/stale actions and claims outside their original scheduled day are rejected. Use the structured `payload` returned by the claim, which renders the current approved SMS text. Do not send the earlier planning response's text.
2. For `kind=reminder`, send the verified international phone and configured text through MessageBird. Report provider acceptance as `submitted`, never handset delivery.
3. POST `/hooks/actions/{id}/finish` with `{status:"submitted"}` for SMS acceptance. Use `failed` for a confirmed failure or `uncertain` if the effect is unknown. Do not automatically replay an in-progress/uncertain action.

If a renewal arrives while an external action is running, the result is held as uncertain. A paid renewal in grace or inactive status creates an operator task to check and, if required, restore access in e-sales. This release cannot plan, claim or requeue a suspension action. Historical processing access outcomes are held as uncertain with a manual review task and do not change recorded merchant access. The feed stays disabled until accepted. An older stored policy with a different lead day requires fresh approval of the one-day schedule. Operator retries are refused outside the reminder's original scheduled day.

A claim is deliberately single-use. After a lost claim response, do not execute the external action based on a repeated request. A claim older than five minutes is moved into uncertain review by the next planning call or the local recover-actions command. Pause the Make consumer and inspect its execution plus the actual merchant/provider result. Recovery never requeues SMS or access changes automatically.

Planning records an activity event when an unclaimed queued reminder is cancelled after its scheduled day or a merchant-state change. There is no catch-up send. A reviewed same-day retry refreshes the approved text and verified phone. Processing and finished payloads remain intact unless the operator explicitly requeues a held action.

Policy disablement, merchant edits and paid renewals also record each cancelled action immediately. An invalid or unverified stored policy keeps automation off and creates one visible issue when the action feed is checked; saving a valid reviewed policy resolves that issue. If the feed is checked again while a previously cancelled reminder is eligible on its original day, it creates an additional review task. Operators can also open Settings and review a cancelled reminder directly without waiting for a feed check. A retry requires deliberate same-day review of the contact and earlier outcome. The existing once-daily Make schedule is retained; this release adds no background poller.

## Switch and rollback

Preserve current scenario blueprints and source exports. Test the replacement with a test inbox and the isolated 1ticks-test copy. Confirm that merchant matching, receipt PDFs, totals, discounts, duplicate prevention, retry and date authority work. Disable the superseded receipt sender before enabling the replacement; never run old/new senders concurrently. Replace Pipedrive reminders with the claimed reminder feed. Retire the Lost-event dependency separately, retaining manual e-sales access management.

For rollback, stop new intake/sending first, preserve its database and queued events, reconcile issued order references/receipts, and decide explicitly which unprocessed events the old path may handle. Re-enabling an old scenario and replaying its backlog blindly can duplicate receipts. Actual deployment, production scenario edits and cancellation require a separate deliberate cutover.
