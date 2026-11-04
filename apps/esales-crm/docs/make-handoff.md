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

## Daily reminders and access

After timing/message/access behavior is confirmed, enable the operator policy. At the agreed Macau time Make POSTs `/hooks/actions/plan` with the intake Bearer key. The response is the durable queued action list. For each item:

1. POST `/hooks/actions/{id}/claim` with the same key. Only a successful claim authorizes proceeding. Renewed/cancelled/stale actions are rejected. The claim response includes its structured `payload`.
2. For `kind=reminder`, send the verified international phone and configured text through MessageBird. Report provider acceptance as `submitted`, never handset delivery.
3. For `kind=suspend`, call the existing e-sales updateMerchant with merchant code and `inactive`. Then separately read getMerchant to verify `status=inactive`. Do not use clearMerchantData.
4. POST `/hooks/actions/{id}/finish` with `{status:"submitted",observed_status:"inactive"}` for a verified suspension, or `{status:"submitted"}` for SMS acceptance. Use `failed` for a confirmed failure or `uncertain` if the effect is unknown. Do not automatically replay an in-progress/uncertain action.

If a renewal arrives while an external action is running, the result is held as uncertain and the operator must verify actual access. A paid renewal for an already-suspended account creates a restoration task; no claim is made that updating CRM dates alone restores login. Verify that behavior on an isolated test merchant before activation. At end of build the feed is off and no Make action has been called.

A claim is deliberately single-use. After a lost claim response, do not execute the external action based on a repeated request. A claim older than five minutes is moved into uncertain review by the next planning call or the local recover-actions command. Pause the Make consumer and inspect its execution plus the actual merchant/provider result. Recovery never requeues SMS or access changes automatically.

## Switch and rollback

Preserve current scenario blueprints and source exports. Test the replacement with synthetic data, a test inbox and an isolated merchant where needed. Confirm that merchant matching, receipt PDFs, totals, discounts, duplicate prevention, retry and date authority work. Disable the superseded receipt sender before enabling the replacement; never run old/new senders concurrently. Replace the Pipedrive-based reminder and Lost-event dependencies with the claimed action feed only after access tests pass.

For rollback, stop new intake/sending first, preserve its database and queued events, reconcile issued order references/receipts, and decide explicitly which unprocessed events the old path may handle. Re-enabling an old scenario and replaying its backlog blindly can duplicate receipts. Actual deployment, production scenario edits and cancellation require a separate deliberate cutover.
