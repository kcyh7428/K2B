from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from server.api import create_app
from server.config import Settings, hash_password


def client_for(store, tmp_path, environment='staging'):
    store.save_merchant({'code':'1ticks','name':'Live internal merchant',
        'service_end':'2027-12-22','access_status':'active'})
    store.save_merchant({'code':'1ticks-test','name':'1TICKS (TEST COPY)',
        'billing_name':'Internal test','email':'test@example.test',
        'service_end':'2026-11-10','currency':'MOP'})
    return TestClient(create_app(Settings(data_dir=tmp_path,public_url='http://testserver',
        password_hash=hash_password('synthetic test password'),
        intake_secret='x'*40,intake_environment=environment)))


@pytest.mark.parametrize('product',['monthly','etoken'])
def test_real_internal_purchase_updates_only_copy_and_replay_is_once(store,event,tmp_path,product):
    client=client_for(store,tmp_path)
    event['data']['merchant_code']='1ticks'
    event['data']['items'][0]['system_product_type']=product
    event['data']['merchant_subscription_end_date']='2028-12-22'
    original=deepcopy(event)
    headers={'Authorization':'Bearer '+'x'*40}
    response=client.post('/hooks/esales/1ticks-test',json=event,headers=headers)
    assert response.status_code==200 and response.json()['state']=='accepted'
    replay=client.post('/hooks/esales/1ticks-test',json=event,headers=headers)
    assert replay.json()['duplicate'] is True
    assert store.one('SELECT count(*) n FROM receipts')['n']==1
    assert store.one("SELECT service_end FROM merchants WHERE code='1ticks-test'")['service_end']==('2028-12-22' if product=='monthly' else '2026-11-10')
    assert store.one("SELECT service_end,access_status FROM merchants WHERE code='1ticks'")=={'service_end':'2027-12-22','access_status':'active'}
    order=store.one('SELECT environment,reference,merchant_code,total_minor FROM orders')
    assert order=={'environment':'staging','reference':'sample-order','merchant_code':'1ticks-test','total_minor':1540000}
    billing=json.loads(store.one('SELECT snapshot FROM receipts')['snapshot'])['billing']
    assert billing['email']=='test@example.test' and billing['billing_name']=='Internal test'
    assert store.one('SELECT recipient FROM deliveries')['recipient'] is None
    assert store.one("SELECT count(*) n FROM events WHERE action='order.intake_source'")['n']==1
    assert event==original
    context=client.get('/hooks/merchants/1ticks-test/renewal',headers=headers).json()
    assert context['service_end']==('2028-12-22' if product=='monthly' else '2026-11-10')


@pytest.mark.parametrize('change,status',[
    ('unauthenticated',401),('production_server',404),('other_merchant',422),
    ('staging_source',422),('pending',422),('bad_amount',422),('missing_data',422),
])
def test_internal_bridge_rejects_unapproved_sources_without_purchase(store,event,tmp_path,change,status):
    client=client_for(store,tmp_path,'production' if change=='production_server' else 'staging')
    event['data']['merchant_code']='1ticks'
    headers={'Authorization':'Bearer '+'x'*40}
    if change=='unauthenticated':headers={}
    elif change=='other_merchant':event['data']['merchant_code']='SAMPLE'
    elif change=='staging_source':event['env']='staging'
    elif change=='pending':event['data']['status']='pending'
    elif change=='bad_amount':event['data']['payment_price']=1
    elif change=='missing_data':event.pop('data')
    assert client.post('/hooks/esales/1ticks-test',json=event,headers=headers).status_code==status
    assert store.one('SELECT count(*) n FROM orders')['n']==0
    assert store.one('SELECT count(*) n FROM receipts')['n']==0


def test_bridge_requires_seeded_test_copy(store,event,tmp_path):
    client=client_for(store,tmp_path)
    with store.transaction() as c:c.execute("DELETE FROM merchants WHERE code='1ticks-test'")
    event['data']['merchant_code']='1ticks'
    response=client.post('/hooks/esales/1ticks-test',json=event,headers={'Authorization':'Bearer '+'x'*40})
    assert response.status_code==409
    assert store.one('SELECT count(*) n FROM orders')['n']==0


def test_forwarded_source_audit_failure_rolls_back_purchase(store,event,tmp_path):
    client=client_for(store,tmp_path)
    with store.transaction() as c:
        c.execute("CREATE TRIGGER reject_source_audit BEFORE INSERT ON events WHEN NEW.action='order.intake_source' BEGIN SELECT RAISE(FAIL,'audit unavailable'); END")
    event['data']['merchant_code']='1ticks'
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        client.post('/hooks/esales/1ticks-test',json=event,headers={'Authorization':'Bearer '+'x'*40})
    assert store.one('SELECT count(*) n FROM orders')['n']==0
    assert store.one('SELECT count(*) n FROM receipts')['n']==0
    assert store.one("SELECT service_end FROM merchants WHERE code='1ticks-test'")['service_end']=='2026-11-10'


def test_readiness_requires_both_receipt_fonts(store,tmp_path,monkeypatch):
    from server import documents
    client=client_for(store,tmp_path)
    assert client.post('/api/login',json={'password':'synthetic test password'},
        headers={'Origin':'http://testserver'}).status_code==200
    assert client.get('/api/readiness').json()['checks']['receipt_font'] is True
    monkeypatch.setattr(documents,'SEMIBOLD_FONT',tmp_path/'missing.ttf')
    assert client.get('/api/readiness').json()['checks']['receipt_font'] is False


@pytest.mark.parametrize('problem',['email','billing_name','currency','issuer'])
def test_held_test_purchase_is_visible_and_returns_error_to_make(store,event,tmp_path,problem):
    client=client_for(store,tmp_path)
    if problem=='issuer':
        with store.transaction() as c:c.execute('UPDATE businesses SET issue_enabled=0')
    else:
        store.save_merchant({'code':'1ticks-test',problem:'HKD' if problem=='currency' else ''})
    event['data']['merchant_code']='1ticks'
    headers={'Authorization':'Bearer '+'x'*40}
    for attempt in range(2):
        response=client.post('/hooks/esales/1ticks-test',json=event,headers=headers)
        assert response.status_code==409 and response.json()['state']=='review'
        assert response.json()['duplicate'] is bool(attempt)
    assert store.one('SELECT count(*) n FROM orders')['n']==1
    assert store.one('SELECT count(*) n FROM exceptions WHERE resolved_at IS NULL')['n']==1
    assert store.one('SELECT count(*) n FROM receipts')['n']==0
