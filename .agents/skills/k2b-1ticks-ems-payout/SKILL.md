---
name: k2b-1ticks-ems-payout
description: Use when Keith asks for 1TICKS EMS event payout settlements, Impecc equipment-order reconciliation, payout PDFs, 1TICKS fee income, or annual income comparisons.
---

# 1TICKS EMS Payout Settlement

Prepare annual event settlements for Impecc Event Production Co. Ltd., and calculate
1TICKS income separately. Keith confirmed these business rules and PDF preferences
on 4 November 2026. Follow `AGENTS.md`; this skill does not authorize payments,
mailbox sends, Airtable writes, credential transfer, schedules, or code delivery.

## Retrieve and bind the source

Read [references/data-and-examples.md](references/data-and-examples.md) for Airtable
access, field meanings, source links, and historical checks. Use current records
for a new settlement, rather than reusing historical totals.

- Base: **1TICKS EMS Reporting**, `appHlFORPsoZPSsLm`; table: `payments`.
- Discover the live schema and year-specific view, then paginate all records.
  Verify event names, production environment, MOP currency, original order dates,
  paid statuses, unique order codes and payment methods. A named view or
  import-year formula alone is insufficient proof of the settlement scope.
- Keep source exports private and unchanged. Record snapshot time and distinguish
  original Airtable figures from reconciliation. Never put tokens in the skill,
  exports, PDFs, chat, Git, or persistent agent-memory files. Transient process
  memory for an authorized HTTPS request is permitted.
- Request known cancellations, refunds and corrections once if not already covered.
  No known adjustments permits calculation from current records; it does not mean
  a payout has been paid or accepted by Impecc.
- When that year already has a prepared or issued statement, compare the fresh
  event figures with it. Explain changed source records, fees or adjustments to
  Keith before circulating a replacement. Preserve the earlier statement and
  identify the revision; do not silently reuse an issued reference for changed figures.
- Retain each run's source time/hash, tier evidence, adjustment ledger, statement
  status and output filenames in a mode-0600 JSON record under
  `~/.local/state/k2b/1ticks-ems/<year>/<run-id>.json`. Read existing local records
  and create the `1ticks-ems` and per-year directories with mode 0700.
  Read them before repeating a year. These records are machine-local, not automatically
  shared with the other Mac; missing counterpart evidence is unknown, not confirmation.
  Keep the earlier record and reference it in a revision rather than rewriting history.

## Calculate the payout

Use `Decimal` from string values and `ROUND_HALF_UP` to two decimal places.

1. Identify equipment orders using `payment_type=order`, supported by `products`.
   The existing `類別` formula labels any empty-products row as Deposit, including
   activation fees. Do not use that label alone to decide the business type.
2. Platform fee: **3.5% of every valid equipment order placed through 1TICKS**,
   rounded on each order before adding. Include card, bank-transfer and offline
   orders, including admin-entered orders. A zero stored fee is not an exemption
   or proof of cancellation. Compare recalculated fees with stored fees.
3. In the established Impecc workflow, **1TICKS receives card payments**.
   Bank-transfer and offline equipment payments go directly to Impecc. Include
   them in order totals and fees, then subtract them as already paid to Impecc
   when calculating funds to remit. Investigate unfamiliar methods or a changed
   collection arrangement rather than guessing who holds the money.
4. Deposits and exhibition activation fees are outside this equipment payout.
   Known upload/waived records contribute zero. A card deposit, unexpected
   non-equipment `Payout`, or contradictory row needs investigation before inclusion.
5. Apply the **EMS service charge once per event**, based on the exhibitor tier:

   | Exhibitors | EMS charge, MOP |
   |---|---:|
   | 1-100 | 4,500.00 |
   | 101-250 | 5,800.00 |
   | Above 250 | 7,500.00 |

   Verify the tier from registration evidence or Keith's confirmed fee. Payment
   rows are not exhibitor counts; companies may have several orders or booths.
   Record the count source or Keith's confirmed fee for every event. Distinct
   company names can suggest a tier but are not verified exhibitor counts.
   If that proxy is the only evidence, mark the EMS charge and resulting payout
   provisional and the PDF a draft pending tier confirmation. Do not count order rows.
6. **Net payout = funds held by 1TICKS - platform fees - EMS service charge.**
   For the established unadjusted workflow, funds held equal gross card collections.
   Negative event payouts offset positive events in the annual summary.
   Present them as net offsets, not standalone payment demands on Impecc. Only
   the combined annual balance is the amount payable; investigate a negative
   annual balance with Keith before treating it as a debt or requesting payment.
7. Maintain a separate adjustment ledger tied to event/order, source, reason,
   original amount, revised amount and effect on fees or funds. Remove confirmed
   cancelled orders from the fee basis. For refunds establish who refunded the
   customer and whether 1TICKS funds changed; do not subtract the same refund twice.
   Bank processing charges do not automatically reverse on cancellation or refund.

For historical reconstruction, retain accepted manual corrections separately from
the calculation under current rules. Explain discrepancies; do not force original
records to match a final total or overwrite Airtable.

## Calculate 1TICKS income

- Use actual per-transaction bank charges when available. Otherwise estimate
  **2.5% of each card transaction, rounded per transaction**, and label it estimated.
  Label income, net-card-fee subtotals and comparison differences derived from
  estimated bank costs as estimated in their table headings or adjacent wording.
- Net card fee income = card platform fees minus card bank processing costs.
- Full income after bank charges = **all platform fees + EMS charges - bank costs**.
  Non-card order fees are income too, without a card processing deduction.
- For requests specifically about net card fees plus EMS, show that subtotal and
  distinguish it from full income. These figures precede other expenses and tax.
- Bank charges are a cost to 1TICKS. **Do not deduct them again from Impecc's payout.**
- Annual comparisons need consistent original/reconciled bases. Name the basis and
  show both total-year and same-event comparisons when the event lineup changes.

## Produce the approved PDF format

Use the available PDF skill and bundled document runtime. Reuse
[assets/1ticks-logo.png](assets/1ticks-logo.png), extracted from Keith's original
statement, with the original orange logo and teal `#00899c` table sections.

- US Letter portrait; one statement per event, then one annual summary page.
- Issuer: Wewon Information Technology Company Limited;
  NO.29C, EDIF. SON KENG TOI, MACAU.
- Payee: Impecc Event Production Co. Ltd.; Avenida Praia Grande No. 369-371 Edf. Ken Ou
  16, Andar C, Macau; +853 2855 5000. Use newer details if supplied.
- Header: preparation date, payout reference, source-backed order period, event
  and payee. Preserve known numbering; distinguish newly proposed references
  from issued statements. Do not reuse previous-year dates.
- Preserve earlier PDFs. Use a new revision filename for replacement statements
  and their annual summary, and include `DRAFT` in filenames and visible page
  labels while any fee tier or other material input is provisional. Record which
  earlier filenames the revision supersedes; do not overwrite or delete them.
- Item / Rate / Amount table: offline orders, bank-transfer orders, card orders,
  less amounts directly paid to Impecc, funds held subtotal; then equipment fees,
  any separately identified agreed correction, EMS charge with tier schedule,
  total fees and prominent net payout. Use MOP and two decimal places.
- Summary: payee, year/date, event / reference / net payout table, and combined
  amount payable. Keith removed **Payment confirmation** and **Calculation basis**:
  do not add these sections back. Keep bank costs and 1TICKS income analysis out
  of the client PDF. Do not add an unrequested transaction appendix.
- Do not claim paid, settled or confirmed receipt for a newly prepared payout.
  Historical reproductions should identify themselves and the agreed basis.
- Independently recompute fees and bank costs per order/transaction, not from
  aggregate subtotals; aggregate rounding can legitimately differ by cents.
  Recompute event/annual arithmetic, extract final PDF text and
  inspect rendered pages for clipping or overlap. Perform the circulation-draft
  wording scan required by `AGENTS.md`. Save under `output/pdf/` and provide the
  PDF. Generation does not authorize an email draft, send or bank transfer.
