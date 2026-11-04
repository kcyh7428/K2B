from fastapi.testclient import TestClient
from server.api import create_app
from server.config import Settings,hash_password


def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path,public_url='http://testserver',password_hash=hash_password('test password long enough'),intake_secret='x'*40)))


def login(c):
    assert c.post('/api/login',json={'password':'test password long enough'},headers={'Origin':'http://testserver'}).status_code==200


def test_private_api_requires_session_and_cross_origin_mutations_rejected(tmp_path):
    c=client(tmp_path)
    assert c.get('/api/merchants').status_code==401
    assert c.post('/api/login',json={'password':'test password long enough'},headers={'Origin':'https://evil.example'}).status_code==403
    login(c)
    assert c.get('/api/merchants').status_code==200
    assert c.post('/api/merchants',json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/logout',headers={'Origin':'http://testserver'}).status_code==200
    assert c.get('/api/merchants').status_code==401


def test_intake_key_cannot_read_customer_api_and_wrong_environment_fails(tmp_path,event):
    c=client(tmp_path)
    assert c.get('/api/merchants',headers={'Authorization':'Bearer '+'x'*40}).status_code==401
    assert c.post('/hooks/esales',json=event).status_code==401
    event['env']='staging'
    assert c.post('/hooks/esales',json=event,headers={'Authorization':'Bearer '+'x'*40}).status_code==422


def test_manual_order_requires_confirmation_and_receipt_download_is_private(tmp_path,event):
    c=client(tmp_path);login(c);origin={'Origin':'http://testserver'}
    merchant={'code':'SAMPLE','name':'Sample','billing_name':'Company','email':'person@example.test','currency':'MOP','service_end':'2026-10-22'}
    assert c.post('/api/merchants',json=merchant,headers=origin).status_code==200
    c.put('/api/settings',json={'issuer':{'name':'Sample','address':'Macau','email':'billing@example.test'},'prefix':'TEST-','issue_enabled':True},headers=origin)
    assert c.post('/api/orders',json={'payload':event},headers=origin).status_code==422
    assert c.post('/api/orders',json={'payload':event,'payment_confirmed':True,'reason':'Verified synthetic transfer'},headers=origin).status_code==200
    receipt=c.get('/api/orders').json()[0]['receipt_id']
    assert c.get('/api/receipts/'+receipt+'/pdf').content.startswith(b'%PDF')
    c.post('/api/logout',headers=origin)
    assert c.get('/api/receipts/'+receipt+'/pdf').status_code==401


def test_no_raw_passwords_or_keys_in_settings_and_export(tmp_path):
    c=client(tmp_path);login(c)
    assert 'password_hash' not in c.get('/api/settings').text
    assert 'x'*40 not in c.get('/api/export').text


def test_login_rate_limit_is_enforced(tmp_path):
    c=client(tmp_path)
    for _ in range(8):c.post('/api/login',json={'password':'wrong'},headers={'Origin':'http://testserver'})
    assert c.post('/api/login',json={'password':'wrong'},headers={'Origin':'http://testserver'}).status_code==429


def test_exception_resolution_is_audited_and_cannot_hide_held_payment(tmp_path,event):
    c=client(tmp_path);login(c);origin={'Origin':'http://testserver'}
    c.post('/hooks/esales',json=event,headers={'Authorization':'Bearer '+'x'*40})
    item=c.get('/api/exceptions').json()[0]
    assert c.post('/api/exceptions/'+item['id']+'/resolve',json={'reason':'Ignore this payment'},headers=origin).status_code==422
    assert c.get('/api/exceptions').json()[0]['resolved_at'] is None


def test_authenticated_readiness_reports_configuration_gaps(tmp_path):
    c=client(tmp_path)
    assert c.get('/api/readiness').status_code==401
    login(c)
    result=c.get('/api/readiness')
    assert result.status_code==200
    assert result.json()['checks']['smtp_configured'] is False
