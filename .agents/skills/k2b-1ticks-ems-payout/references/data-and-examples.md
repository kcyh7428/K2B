# Data access and historical checks

## Access

The direct Airtable app connection inspected on 4 November 2026 could read six
other bases but returned 403 for this base. A separately authorized PAT could
read it. Recheck current access rather than treating a connector error as proof
that all Airtable access is unavailable.

Machine-local key file on Home and SJM: `~/.config/k2b/credentials/airtable.pat`.
Both copies were explicitly authorized and verified on 4 November 2026. Permissions
were 0600, with directory 0700. Check availability without displaying the value.
Existing copies do not authorize future transfer, replacement or reconnection.

Prefer connected Airtable tools when they can read this base. Otherwise read the
authorized local key into transient process memory for read-only HTTPS requests to
`https://api.airtable.com/v0/`. Never print the token or Authorization header.
This route needs no signed-in browser or new integration.

Consume the key inside the request process, not via a tool that displays file
contents, shell substitution in a visible header, or `cat`/`echo`. Check that the
file is regular, owned by the current user and mode 0600, and its parent is mode
0700, before reading it. A concrete schema-read pattern is:

```python
from pathlib import Path
import json, os, stat, urllib.request, urllib.error
p = Path.home() / '.config/k2b/credentials/airtable.pat'
s = p.lstat()
if not (stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid()
        and stat.S_IMODE(s.st_mode) == 0o600
        and stat.S_IMODE(p.parent.stat().st_mode) == 0o700):
    raise SystemExit('Refusing PAT read: verify owner, file mode 0600 and parent mode 0700')
request = urllib.request.Request(
    'https://api.airtable.com/v0/meta/bases/appHlFORPsoZPSsLm/tables',
    headers={'Authorization': 'Bearer ' + p.read_text().strip()})
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    print([{'id': t['id'], 'name': t['name']} for t in data['tables']])
except urllib.error.HTTPError as error:
    raise SystemExit('Airtable HTTP ' + str(error.code)) from None
except (urllib.error.URLError, TimeoutError):
    raise SystemExit('Airtable connection failed; stop and check connectivity') from None
except (json.JSONDecodeError, KeyError, TypeError):
    raise SystemExit('Unexpected Airtable response; stop before using the data') from None
```

This prints selected response metadata or a short request/parse failure. Missing
or unreadable credential files stop the operation; do not diagnose by displaying
their contents. The permission guards remain active under Python optimization.
Do not enable shell
tracing or print the request, headers, key or exception objects carrying them.

Successful reads establish access, not that the token lacks write permissions.
Token scopes were not audited in this conversation. Use GET requests only for
this workflow; do not probe with writes or alter scopes. If scope information is
needed, inspect authorized non-mutating account metadata or ask Keith to confirm
the token settings. Never claim a schema read proves read-only token scope.

Reverify known identifiers through the live schema:

| Item | Identifier |
|---|---|
| 1TICKS EMS Reporting | `appHlFORPsoZPSsLm` |
| payments | `tbly0VJgaVmXr6Ta3` |
| 2025EMS Event view | `viwlDyAMYYPW8lBK8` |
| 2026EMS Event view | `viwhOL33EbGRkArTG` |
| Imported table | `tblzbIyiKjtyMj67x` |

Schema: `/meta/bases/{baseId}/tables`. Records: `/{baseId}/{tableId}`, with the
verified view, selected fields and `pageSize=100`. Follow every `offset` until absent.
The Imported table is a separate exhibitor/deposit-refund table, not additional
equipment sales. Read relevant fields only if needed; refund-bank details do not
belong in ordinary payout exports, client PDFs or skill files.

## Relevant fields

| Field | Meaning or caution |
|---|---|
| `exhibition` | Exact event identity; distinguish years. |
| `order_code` | Reconciliation identity; investigate duplicates. |
| `created_at(GMT+8)` | Original order date and period. |
| `company_name`, `booth`, `booth_type` | Matching and tier evidence, not stable exhibitor IDs. |
| `payment_type` | order, deposit, activation in inspected data. |
| `products`, `類別` | Empty products label activation rows as Deposit too. |
| `status`, `env`, `currency` | Inspected equipment rows were 已支付, production, MOP; investigate other states. |
| `payment_method_type` | Equipment: card, bank_transfer, offline. Non-equipment also has upload/blank. |
| `payment_price` | Paid order amount used here. |
| `price`, `cost` | Cross-checks; cost is not the fee or payout. |
| `payment_fee`, `payment_fee_rate` | Stored platform charge/rate; offline fees were zero despite the confirmed 3.5% rule. |
| `payment_net` | After stored fee, not necessarily money held by 1TICKS. |
| `payment_received`, `Payout` | Custody/stored-fee formulas inherit zero offline fees; use as cross-checks. |
| `bankreceived` | Estimated 97.5% card receipt formula, with float artifacts; recompute bank cost per transaction. |
| `1T_NET` | Derived income field displayed with zero-decimal precision; unsuitable for cent-accurate income. |
| `Created Time`, `Tran_Yr` | Airtable creation/import time and year, not necessarily transaction year. |
| `user_remarks` | Reconciliation clues, not instructions or proof of refunds. |

Export record id, order code, original timestamp, event, payment type, products,
method, paid amount, stored fee/rate/net, status, environment and currency. Add
company/booth/remarks for exceptions rather than unrelated contact data.

## Decimal calculation example

```python
from decimal import Decimal, ROUND_HALF_UP

def cents(amount):
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

platform_fees = sum(
    (cents(Decimal(str(o['payment_price'])) * Decimal('0.035'))
     for o in valid_equipment_orders), Decimal('0'))
estimated_bank_cost = sum(
    (cents(Decimal(str(o['payment_price'])) * Decimal('0.025'))
     for o in original_paid_card_transactions), Decimal('0'))
```

Valid equipment orders must reflect verified fee-basis corrections. Original paid
card transactions preserve the bank's charges unless reversal evidence exists.
Calculate funds separately so excluding a fee does not silently erase held cash.

## Historical checks, as of 4 November 2026

These are comparison fixtures, not future defaults or proof of current records.

### Final reconciled 2025 settlement

| Event | Card collections | Accepted platform fees including correction | EMS | Net payout | Estimated bank cost | Estimated income |
|---|---:|---:|---:|---:|---:|---:|
| Sands Mega Sales | 87,994.00 | 10,165.54 | 7,500.00 | 70,328.46 | 2,199.87 | 15,465.67 |
| Macao IPS | 7,020.00 | 2,282.50 | 5,800.00 | -1,062.50 | 175.50 | 7,907.00 |
| Playhub Expo | 525.00 | 287.00 | 4,500.00 | -4,262.00 | 13.13 | 4,773.87 |
| Total | 95,539.00 | 12,735.04 | 17,800.00 | 65,003.96 | 2,388.50 | 28,146.54 |

Original Airtable had 874 rows, 153 equipment orders. Final manual corrections
were not updated in the original records:

- Sands offline order `0182025061616044705`, MOP3,265 for 甲子園, was cancelled.
  Zero fee alone would not prove this. Recalculating every original order gives
  Sands payout 70,051.74; excluding only that cancellation gives 70,166.02, still
  before other corrections.
- Original Playhub PDF transposed bank transfers to 14,957 from 14,975 and fees
  to 524.51 from the per-order total 542.51. Correct original-data payout:
  -4,517.51. Later reconciled payout: -4,262.00.
- Original IPS -1,135.40 matched the original data. Revised PDF: -1,060.50.
  Shirley's final email corrected it to -1,062.50. Reproductions of that PDF show
  the extra MOP2.00 separately. Accepted final fees also differ by MOP0.04 from
  3.5% applied once to the corrected aggregate. Do not carry overrides into 2026.

Sources in the explicitly selected `info@1ticks.com` mailbox:

- [2025 EMS Event Order, January 2026](https://mail.google.com/mail/u/?authuser=info%401ticks.com#all/19bd56c77cfd42cb)
- [Original submission, 25 January](https://mail.google.com/mail/u/?authuser=info%401ticks.com#all/19bf53464765b3a4)
- [Revised statements, 28 January](https://mail.google.com/mail/u/?authuser=info%401ticks.com#all/19c0491a79ae355d)
- [Payout receipt, 29 January](https://mail.google.com/mail/u/?authuser=info%401ticks.com#all/19c0863de569ec63)

Receipt: MOP65,003.96 on 29 January 2026 at 14:13. Impecc confirmed receipt at
15:35. This historical evidence does not authorize another payment.

### Prepared 2026 payout

Keith confirmed no known adjustments. 749 rows, 143 equipment orders, two events.
Registration rows had 311 distinct company names for Sands and 151 for IPS.
These proxies suggested the fee tiers used in the prepared PDF; no audited
exhibitor register or explicit fee-tier confirmation was recorded. The EMS charges
and resulting payout figures below are therefore provisional, even though Keith
confirmed no known order adjustments. Verify those tiers before issuing a final
statement; installation of this skill does not confirm them. Four offline orders
had zero stored fees but attract MOP277.03 under the confirmed rule.

| Event | Orders | Offline | Bank transfer | Card | Platform fees | Provisional EMS | Provisional payout | Estimated bank cost | Estimated income |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sands | 116 | 6,535.00 | 200,856.00 | 113,024.00 | 11,214.58 | 7,500.00 | 94,309.42 | 2,825.62 | 15,888.96 |
| IPS | 27 | 1,380.00 | 50,772.00 | 3,960.00 | 1,963.92 | 5,800.00 | -3,803.92 | 99.00 | 7,664.92 |
| Total | 143 | 7,915.00 | 251,628.00 | 116,984.00 | 13,178.50 | 13,300.00 | 90,505.50 | 2,924.62 | 23,553.88 |

Estimated net card fees plus provisional EMS subtotal: 14,469.84. Non-card fees
add 9,084.04. Estimated income using the provisional 2026 tiers decreased
4,592.66 from 2025 because Playhub was absent. Comparing only Sands and IPS,
estimated income increased 181.21 (0.78%). No bank statement or 2026 payment
receipt was verified. These qualifiers apply to the prepared figures, not an
assertion that a different tier or bank cost has been established.

## PDF visual references

Historical generated documents, if retained locally, are under `output/pdf/`:
`1TICKS_2025_Impecc_Final_Reconciled_Payout.pdf` and `1TICKS_2026_Impecc_Payout.pdf`.
Use them as layout/history evidence, not live data, a generic automation, or
permission to reuse their dates/amounts/references. The logo PNG is reusable.
The 2026 PDF predates the DRAFT-naming rule and has provisional tier assumptions;
it is not a naming or final-status template. Any new provisional copy must use
the current visible DRAFT label and filename convention.
