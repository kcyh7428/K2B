import json
import pytest
from server.documents import render_receipt
from server.orders import ingest,release
from server.delivery import retry_delivery,process_delivery
from server.config import Settings


def test_maximum_chinese_description_renders_across_pages(store,event,tmp_path):
    event['data']['items'][0]['system_product_name']='續期服務'*500
    ingest(store,event)
    row=store.one('select snapshot from receipts')
    render_receipt(json.loads(row['snapshot']),tmp_path/'long.pdf')
    assert (tmp_path/'long.pdf').read_bytes().startswith(b'%PDF')


def test_recipient_correction_is_explicit_without_mutating_receipt(store,event,tmp_path):
    ingest(store,event)
    process_delivery(store,Settings(data_dir=tmp_path,demo=True))
    receipt=store.one('select * from receipts')
    retry_delivery(store,receipt['id'],'Billing recipient corrected after bounce',recipient='corrected@example.test')
    assert store.one('select recipient from deliveries')['recipient']=='corrected@example.test'
    assert store.one('select snapshot from receipts')['snapshot']==receipt['snapshot']
    assert store.one('select number from receipts')['number']=='TEST-2026-00001'


def test_stale_save_cannot_overwrite_concurrent_renewal_inside_transaction(store,event):
    original=store.one('select * from merchants')
    ingest(store,event)
    with pytest.raises(ValueError,match='changed'):
        store.save_merchant({'code':'SAMPLE','name':'Edited','service_end':'2026-10-22'},'Stale edit',expected_updated_at=original['updated_at'])
    assert store.one('select service_end from merchants')['service_end']=='2027-10-22'


def test_release_keeps_new_access_restoration_issue_open(store,event):
    store.save_merchant({'code':'SAMPLE','email':'','access_status':'inactive'},'Prepare missing email')
    result=ingest(store,event)
    assert result['state']=='review'
    store.save_merchant({'code':'SAMPLE','email':'correct@example.test'},'Correct billing email')
    release(store,result['order_id'],'Payment and recipient checked')
    assert store.one("select resolved_at from exceptions where kind='access'")['resolved_at'] is None
    assert store.one("select resolved_at from exceptions where kind='review'")['resolved_at'] is not None


def test_long_billing_address_is_renderable(store,event,tmp_path):
    store.save_merchant({'code':'SAMPLE','address':'澳門測試地址'*500},'Long address')
    ingest(store,event)
    render_receipt(json.loads(store.one('select snapshot from receipts')['snapshot']),tmp_path/'address.pdf')
    assert (tmp_path/'address.pdf').exists()


def test_old_email_callback_cannot_change_a_new_attempt(store,event,tmp_path):
    from fastapi.testclient import TestClient
    from server.api import create_app
    from server.config import hash_password
    ingest(store,event);process_delivery(store,Settings(data_dir=tmp_path));store.bind_mode('private')
    row=store.one('select * from deliveries');rid=row['receipt_id']
    with store.transaction() as c:c.execute("update deliveries set attempts=2,state='submitted',message_id=?",(f'<{rid}.2@esales-receipts.local>',))
    client=TestClient(create_app(Settings(data_dir=tmp_path,public_url='http://testserver',password_hash=hash_password('long test password 123'),intake_secret='x'*40,email_hook_secret='y'*40)))
    response=client.post('/hooks/email',json={'message_id':f'<{rid}.1@esales-receipts.local>','status':'bounced'},headers={'Authorization':'Bearer '+'y'*40})
    assert response.status_code==200
    assert store.one('select state from deliveries')['state']=='submitted'
