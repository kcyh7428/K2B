import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from server.orders import ingest, normalize


def test_fixed_discount_renewal_snapshots_billing_and_uses_reported_expiry(store, event):
    result = ingest(store, event)
    assert result['state'] == 'accepted'
    receipt = store.one('select * from receipts')
    assert receipt['number'] == 'TEST-2026-00001'
    assert store.one('select service_end from merchants')['service_end'] == '2027-10-22'
    assert store.one('select total_minor from orders')['total_minor'] == 1540000
    assert store.one('select count(*) n from deliveries')['n'] == 1
    assert 'Sample Company' in receipt['snapshot']


def test_identical_event_and_concurrent_replay_have_one_receipt(store, event):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: ingest(store, copy.deepcopy(event)), range(4)))
    assert store.one('select count(*) n from receipts')['n'] == 1
    assert store.one('select count(*) n from deliveries')['n'] == 1


def test_changed_financial_content_holds_conflict_without_reissuing(store, event):
    ingest(store, event)
    changed = copy.deepcopy(event)
    changed['data']['items'][0]['quantity'] = 6
    changed['data']['price'] = 8400
    changed['data']['payment_price'] = 7000
    assert ingest(store, changed)['state'] == 'conflict'
    assert store.one('select count(*) n from receipts')['n'] == 1
    assert store.one('select total_minor from orders')['total_minor'] == 1540000
    assert store.one('select count(*) n from exceptions')['n'] == 1


def test_topup_does_not_extend_or_clear_overdue_subscription(store, event):
    data = event['data']
    data.update(items_type='etoken', price=400, payment_price=400, coupon_discount=0)
    data['items'] = [{'system_product_type': 'etoken', 'system_product_name': '易代幣 4000枚', 'quantity': 1, 'unit_price': 400}]
    assert ingest(store, event)['state'] == 'accepted'
    assert store.one('select service_end from merchants')['service_end'] == '2026-10-22'
    assert store.one('select kind from orders')['kind'] == 'topup'


def test_six_month_term_is_not_replaced_with_one_year(store, event):
    data = event['data']
    data.update(price=8400, payment_price=8400, coupon_discount=0, merchant_subscription_end_date='2027-04-22')
    data['items'][0]['quantity'] = 6
    ingest(store, event)
    assert store.one('select service_end from merchants')['service_end'] == '2027-04-22'


@pytest.mark.parametrize('change', ['missing-merchant', 'missing-expiry', 'decreasing-expiry', 'mixed'])
def test_ambiguous_orders_are_held_without_receipts(store, event, change):
    data = event['data']
    if change == 'missing-merchant': data['merchant_code'] = 'UNKNOWN'
    if change == 'missing-expiry': del data['merchant_subscription_end_date']
    if change == 'decreasing-expiry': data['merchant_subscription_end_date'] = '2026-01-01'
    if change == 'mixed': data['items'][0]['system_product_type'] = 'other'
    assert ingest(store, event)['state'] == 'review'
    assert store.one('select count(*) n from receipts')['n'] == 0


@pytest.mark.parametrize('key,value', [('payment_price', 100), ('price', 'NaN'), ('coupon_discount', -1), ('payment_price', '15400.001'), ('currency', 'USD'), ('status', 'pending')])
def test_invalid_payload_cannot_have_financial_side_effects(store, event, key, value):
    event['data'][key] = value
    with pytest.raises(ValueError): ingest(store, event)
    assert store.one('select count(*) n from orders')['n'] == 0


def test_replay_uses_original_billing_snapshot(store, event):
    ingest(store, event)
    original = store.one('select snapshot from receipts')['snapshot']
    event['data']['merchant_email'] = 'changed@example.test'
    event['data']['merchant_name'] = 'Changed'
    assert ingest(store, event)['duplicate'] is True
    assert store.one('select snapshot from receipts')['snapshot'] == original
