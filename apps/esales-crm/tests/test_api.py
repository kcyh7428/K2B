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


def test_renewal_hook_reads_current_date_and_limits_exposed_fields(tmp_path,monkeypatch):
    from datetime import date,datetime,timedelta
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,9,12,tzinfo=tz)
    monkeypatch.setattr('server.api.datetime',FixedClock)
    c=client(tmp_path)
    login(c)
    merchant={'code':'1ticks-test','name':'1TICKS TEST','billing_name':'Private company','email':'private@example.test','phone':'+85363997300','service_end':'2026-11-10','access_status':'active'}
    assert c.post('/api/merchants',json=merchant,headers={'Origin':'http://testserver'}).status_code==200
    path='/hooks/merchants/1ticks-test/renewal'
    assert c.get(path).status_code==401
    headers={'Authorization':'Bearer '+'x'*40}
    result=c.get(path,headers=headers)
    assert result.status_code==200
    today=date(2026,11,9)
    assert result.json()=={'code':'1ticks-test','name':'1TICKS TEST','service_end':'2026-11-10','access_status':'active','phone':'+85363997300','as_of':'2026-11-09','days_until_end':1,'renewal_state':'due_soon'}
    assert c.get('/hooks/merchants/missing/renewal',headers=headers).status_code==404
    row=c.get('/api/merchants').json()[0]
    merchant.update(service_end='2027-11-10',expected_updated_at=row['updated_at'],reason='Approved renewal')
    assert c.put('/api/merchants/'+row['id'],json=merchant,headers={'Origin':'http://testserver'}).status_code==200
    assert c.get(path,headers=headers).json()['service_end']=='2027-11-10'
    from server.db import Store
    store=Store(tmp_path/'crm.sqlite')
    for delta,expected in ((-1,'overdue'),(0,'due_soon'),(1,'due_soon'),(30,'due_soon'),(31,'current')):
        with store.transaction() as connection:
            connection.execute('UPDATE merchants SET service_end=? WHERE code=?',((today+timedelta(days=delta)).isoformat(),'1ticks-test'))
        context=c.get(path,headers=headers).json()
        assert context['days_until_end']==delta
        assert context['renewal_state']==expected
    for value in (None,'invalid-date','2020-01-01'):
        with store.transaction() as connection:
            connection.execute('UPDATE merchants SET service_end=? WHERE code=?',(value,'1ticks-test'))
        response=c.get(path,headers=headers)
        assert response.status_code==200
        context=response.json()
        assert context['renewal_state']==('overdue' if value=='2020-01-01' else 'needs_review')
        if value!='2020-01-01':assert context['days_until_end'] is None


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
