import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from server.db import now, uid


def text(value, label, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f'Invalid {label}')
    return value.strip()


def email_ok(value):
    return isinstance(value, str) and len(value) <= 254 and bool(re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', value))


def minor(value):
    if isinstance(value, bool): raise ValueError('Invalid amount')
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d < 0 or d > Decimal('100000000') or d*100 != (d*100).to_integral_value():
            raise ValueError('Amount must be finite, nonnegative and have at most two decimals')
        return int(d*100)
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid amount') from None


def normalize(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict): raise ValueError('Expected env and data object')
    d = payload['data']
    env = text(payload.get('env'), 'environment', 30)
    if env not in ('production','staging','demo','manual'): raise ValueError('Unknown environment')
    if d.get('status') != 'completed': raise ValueError('Only completed orders are accepted')
    currency = d.get('currency')
    if currency not in ('MOP','HKD'): raise ValueError('Unsupported currency')
    code = text(d.get('merchant_code'), 'merchant code', 120)
    ref = text(d.get('code'), 'order reference', 120)
    raw_lines = d.get('items')
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 100: raise ValueError('Expected 1 to 100 items')
    lines = []
    for line in raw_lines:
        if not isinstance(line, dict): raise ValueError('Invalid item')
        qty = line.get('quantity')
        try:
            q = Decimal(str(qty))
            if isinstance(qty, bool) or not q.is_finite() or q < 1 or q > 10000 or q != q.to_integral_value(): raise ValueError('Invalid quantity')
        except InvalidOperation: raise ValueError('Invalid quantity') from None
        rate = minor(line.get('unit_price'))
        lines.append({'type': text(line.get('system_product_type'), 'product type', 60), 'description': text(line.get('system_product_name'), 'description', 2000), 'quantity': int(q), 'unit_minor': rate, 'amount_minor': rate*int(q)})
    kinds = {line['type'] for line in lines}
    kind = 'renewal' if kinds == {'monthly'} else 'topup' if kinds == {'etoken'} else 'other'
    subtotal = sum(line['amount_minor'] for line in lines)
    if subtotal > 10_000_000_000: raise ValueError('Order amount exceeds supported limit')
    discount = minor(d.get('coupon_discount', 0) or 0)
    paid = minor(d.get('payment_price'))
    if discount > subtotal or subtotal-discount != paid: raise ValueError('Paid amount does not match lines less discount')
    if 'price' in d and minor(d['price']) != subtotal: raise ValueError('Subtotal does not match source price')
    end = d.get('merchant_subscription_end_date')
    if end:
        try: end = date.fromisoformat(end).isoformat()
        except (ValueError, TypeError): raise ValueError('Invalid service end date') from None
    payment = d.get('payment')
    if isinstance(payment, dict):
        method = payment.get('payment_method')
        nested_name = method.get('name') if isinstance(method, dict) else None
        payment_label = str(nested_name or payment.get('name') or payment.get('method') or 'Recorded transfer')[:200]
    else:
        payment_label = str(payment or 'Recorded transfer')[:200]
    result = {'environment': env, 'reference': ref, 'merchant_code': code, 'kind': kind, 'currency': currency, 'lines': lines, 'subtotal_minor': subtotal, 'discount_minor': discount, 'total_minor': paid, 'coupon': str(d.get('coupon_code') or '')[:120], 'service_end': end, 'payment_method': payment_label}
    # Billing names/emails are mutable metadata, not a second purchase identity.
    result['fingerprint'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def hold(c, order_id, kind, detail):
    existing = c.execute('SELECT 1 FROM exceptions WHERE order_id=? AND kind=? AND resolved_at IS NULL', (order_id, kind)).fetchone()
    if not existing: c.execute('INSERT INTO exceptions VALUES(?,?,?,?,NULL,NULL,?)', (uid(), order_id, kind, detail, now()))


def issue(c, order_id, order, merchant, business):
    c.execute('UPDATE businesses SET sequence=sequence+1 WHERE id=?', (business['id'],))
    seq = c.execute('SELECT sequence FROM businesses WHERE id=?', (business['id'],)).fetchone()['sequence']
    number = f"{business['prefix']}{seq:05d}"
    while c.execute('SELECT 1 FROM receipts WHERE number=?',(number,)).fetchone():
        seq+=1
        number=f"{business['prefix']}{seq:05d}"
        c.execute('UPDATE businesses SET sequence=? WHERE id=?',(seq,business['id']))
    receipt_id = uid()
    snapshot = {**order, 'number': number, 'issued_date': datetime.now(ZoneInfo('Asia/Macau')).date().isoformat(), 'issuer': json.loads(business['issuer']), 'billing': {k:merchant[k] for k in ('code','name','billing_name','email','address')}, 'tax_minor': 0}
    c.execute('INSERT INTO receipts(id,order_id,number,snapshot,created_at) VALUES(?,?,?,?,?)', (receipt_id, order_id, number, json.dumps(snapshot, ensure_ascii=False), now()))
    c.execute('INSERT INTO deliveries(id,receipt_id,message_id) VALUES(?,?,?)', (uid(), receipt_id, f'<{receipt_id}@esales-receipts.local>'))
    c.execute("UPDATE orders SET state='accepted' WHERE id=?", (order_id,))
    if order['kind'] == 'renewal':
        c.execute('UPDATE merchants SET service_end=?,updated_at=? WHERE id=?', (order['service_end'], now(), merchant['id']))
        c.execute("UPDATE actions SET state='cancelled' WHERE merchant_code=? AND state='queued'",(merchant['code'],))
        if merchant['access_status']=='inactive':hold(c,order_id,'access','Paid renewal for suspended merchant; verify restoration in e-sales')
    return receipt_id


def review_reason(order, merchant, business):
    if not merchant: return 'Merchant code has no approved CRM match'
    if order['kind'] == 'other': return 'Mixed or unrecognized products require review'
    if merchant['currency'] != order['currency']: return 'Currency differs from merchant configuration'
    if not email_ok(merchant['email']): return 'Billing recipient is missing or invalid'
    if not merchant['billing_name'].strip(): return 'Billing identity must be confirmed'
    if not business['issue_enabled']: return 'Issuer configuration has not been approved'
    if order['kind'] == 'renewal':
        if not order['service_end']: return 'Renewal has no e-sales service end date'
        if merchant['service_end'] and order['service_end'] < merchant['service_end']: return 'Reported expiry would shorten service'
    return None


def ingest(store, payload, business='converto', environment=None, manual_reason=None, intake_note=None):
    order = normalize(payload)
    if manual_reason is not None:text(manual_reason,'approval reason',1000)
    if intake_note is not None:text(intake_note,'intake note',1000)
    if environment and order['environment'] != environment: raise ValueError('Environment not allowed on this intake')
    with store.transaction() as c:
        old = c.execute('SELECT * FROM orders WHERE business=? AND environment=? AND reference=?', (business, order['environment'], order['reference'])).fetchone()
        if old:
            if old['fingerprint'] != order['fingerprint']:
                original=json.loads(old['payload'])
                detail=f"Conflicting retry: recorded {old['currency']} {old['total_minor']//100}.{old['total_minor']%100:02d}, received {order['currency']} {order['total_minor']//100}.{order['total_minor']%100:02d}. Service end: recorded {original.get('service_end') or 'not supplied'}, received {order['service_end'] or 'not supplied'}. Original purchase/document unchanged; verify e-sales source and see Activity for the received details."
                hold(c, old['id'], 'conflict', detail)
                c.execute("UPDATE exceptions SET detail=? WHERE order_id=? AND kind='conflict' AND resolved_at IS NULL",(detail,old['id']))
                store.audit(c, 'order.conflict', old['id'], json.dumps({'original_fingerprint':old['fingerprint'],'received':order},ensure_ascii=False))
                return {'order_id':old['id'], 'state':'conflict', 'duplicate':False}
            return {'order_id':old['id'], 'state':old['state'], 'duplicate':True}
        company = c.execute('SELECT * FROM businesses WHERE id=?', (business,)).fetchone()
        if not company: raise ValueError('Issuing business is not configured')
        merchant = c.execute('SELECT * FROM merchants WHERE code=?', (order['merchant_code'],)).fetchone()
        order_id = uid()
        # Retain normalized payload only: no payment image URLs or arbitrary credentials.
        c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (order_id, business, order['environment'], order['reference'], order['merchant_code'], order['kind'], order['fingerprint'], json.dumps(order, ensure_ascii=False), order['total_minor'], order['currency'], 'review', now()))
        reason = review_reason(order, merchant, company)
        if reason: hold(c, order_id, 'review', reason)
        else: issue(c, order_id, order, merchant, company)
        if manual_reason is not None:store.audit(c,'payment.manual_confirmation',order_id,manual_reason)
        if intake_note is not None:store.audit(c,'order.intake_source',order_id,intake_note)
        store.audit(c, 'order.received', order_id, reason or 'Purchase accepted; receipt and delivery reserved once')
        return {'order_id':order_id, 'state':'review' if reason else 'accepted', 'duplicate':False}


def release(store, order_id, reason):
    text(reason, 'review reason', 1000)
    with store.transaction() as c:
        row = c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if not row or row['state'] != 'review': raise ValueError('Only held orders can be released')
        order = json.loads(row['payload'])
        merchant = c.execute('SELECT * FROM merchants WHERE code=?', (order['merchant_code'],)).fetchone()
        business = c.execute('SELECT * FROM businesses WHERE id=?', (row['business'],)).fetchone()
        problem = review_reason(order, merchant, business)
        if problem: raise ValueError(problem)
        receipt_id = issue(c, order_id, order, merchant, business)
        c.execute("UPDATE exceptions SET resolved_at=?,resolution=? WHERE order_id=? AND kind='review' AND resolved_at IS NULL", (now(), reason, order_id))
        store.audit(c, 'order.released', order_id, reason)
        return receipt_id
