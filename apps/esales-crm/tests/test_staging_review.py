import fcntl
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from server.api import create_app
from server.automation import plan_actions, claim_action, save_policy, DEFAULT
from server.config import Settings, hash_password
from server.delivery import process_delivery
from server.documents import ensure_pdf
from server.orders import ingest


def operator(tmp_path, environment='production'):
    app = create_app(Settings(data_dir=tmp_path, public_url='http://testserver',
        password_hash=hash_password('synthetic test password'), intake_secret='x'*40,
        email_hook_secret='y'*40, intake_environment=environment))
    client = TestClient(app)
    assert client.post('/api/login', json={'password':'synthetic test password'},
        headers={'Origin':'http://testserver'}).status_code == 200
    return client


@pytest.mark.parametrize('kind', ['monthly', 'etoken'])
def test_manual_payment_uses_server_staging_environment(store, event, tmp_path, kind):
    event['data']['items'][0]['system_product_type'] = kind
    client = operator(tmp_path, 'staging')
    result = client.post('/api/orders', json={'payload':event, 'payment_confirmed':True,
        'reason':'Synthetic transfer proof reviewed'}, headers={'Origin':'http://testserver'})
    assert result.status_code == 200
    assert store.one('SELECT environment FROM orders')['environment'] == 'staging'
    assert store.one('SELECT COUNT(*) n FROM receipts')['n'] == 1
    assert client.post('/hooks/esales', json=event,
        headers={'Authorization':'Bearer '+'x'*40}).status_code == 422


def test_manual_confirmation_failure_rolls_back_receipt_and_expiry(store, event, tmp_path):
    client = operator(tmp_path)
    with store.transaction() as c:
        c.execute("CREATE TRIGGER reject_confirmation BEFORE INSERT ON events WHEN NEW.action='payment.manual_confirmation' BEGIN SELECT RAISE(FAIL,'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        client.post('/api/orders', json={'payload':event, 'payment_confirmed':True,
            'reason':'Reviewed proof'}, headers={'Origin':'http://testserver'})
    assert store.one('SELECT COUNT(*) n FROM orders')['n'] == 0
    assert store.one('SELECT COUNT(*) n FROM receipts')['n'] == 0
    assert store.one('SELECT sequence FROM businesses')['sequence'] == 0
    assert store.one('SELECT service_end FROM merchants')['service_end'] == '2026-10-22'


@pytest.mark.parametrize('first,last', [('delivered','bounced'), ('bounced','delivered')])
def test_conflicting_terminal_email_callbacks_are_held_without_regression(store, event, tmp_path, first, last):
    ingest(store,event)
    with store.transaction() as c:
        c.execute("UPDATE deliveries SET state='submitted',attempts=1")
    message = store.one('SELECT message_id FROM deliveries')['message_id']
    client = operator(tmp_path)
    headers={'Authorization':'Bearer '+'y'*40}
    assert client.post('/hooks/email', json={'message_id':message,'status':first}, headers=headers).status_code == 200
    response=client.post('/hooks/email', json={'message_id':message,'status':last}, headers=headers)
    assert response.status_code == 200
    assert response.json().get('conflict') is True
    assert store.one('SELECT state FROM deliveries')['state'] == first
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='delivery' AND resolved_at IS NULL")['n'] == 1
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='delivery.callback_conflict'")['n'] == 1


def test_delivered_callback_resolves_the_old_submission_issue(store, event, tmp_path):
    ingest(store,event)
    process_delivery(store,Settings(data_dir=tmp_path))
    with store.transaction() as c:
        c.execute("UPDATE deliveries SET state='submitted'")
    message=store.one('SELECT message_id FROM deliveries')['message_id']
    client=operator(tmp_path)
    assert client.post('/hooks/email', json={'message_id':message,'status':'delivered'},
        headers={'Authorization':'Bearer '+'y'*40}).status_code == 200
    assert store.one('SELECT error FROM deliveries')['error'] is None
    assert store.one('SELECT resolved_at FROM exceptions')['resolved_at'] is not None


@pytest.mark.parametrize('changes', [{'service_end':'22/12/2027'}, {'access_status':'made-up'}])
def test_store_rejects_invalid_merchant_state_without_mutation(store, changes):
    with pytest.raises(ValueError):
        store.save_merchant({'code':'SAMPLE',**changes},'Invalid imported state')
    assert store.one('SELECT service_end FROM merchants')['service_end'] == '2026-10-22'


def test_malformed_existing_expiry_stays_visible_for_operator_review(store,tmp_path):
    with store.transaction() as c:
        c.execute("UPDATE merchants SET service_end='invalid-date'")
    client=operator(tmp_path)
    assert client.get('/api/overview').status_code == 200
    merchant=client.get('/api/merchants').json()[0]
    assert merchant['renewal_state'] == 'needs_review'
    assert merchant['days_until_end'] is None
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    assert plan_actions(store,'2026-10-25') == []
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='merchant_data'")['n'] == 1
    plan_actions(store,'2026-10-25')
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='merchant_data'")['n'] == 1


def test_import_holds_invalid_expiry_before_saving_merchant(store,tmp_path):
    from server.migration import import_audit
    source=tmp_path/'source';source.mkdir()
    (source/'migration-inventory.json').write_text(json.dumps({'merchantChecks':[{'merchantCode':'OTHER'}]}))
    (source/'esales-active-grace-snapshot.json').write_text(json.dumps({'merchants':[{'code':'OTHER','end':'22/12/2027','status':'active'}]}))
    (source/'billing-map.json').write_text(json.dumps([{'Name':'OTHER','Email':'other@example.test'}]))
    result=import_audit(store,source)
    assert result['imported'] == 0 and len(result['held']) == 1
    assert store.one("SELECT id FROM merchants WHERE code='OTHER'") is None


def test_stale_action_claim_is_held_and_never_replayed(store):
    from server.automation import recover_actions
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    action=plan_actions(store,'2026-10-25')[0]
    claim_action(store,action['id'])
    with store.transaction() as c:
        c.execute("UPDATE actions SET claimed_at='2000-01-01T00:00:00+00:00'")
    assert recover_actions(store) == 1
    assert store.one('SELECT state FROM actions')['state'] == 'uncertain'
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='automation'")['n'] == 1
    assert recover_actions(store) == 0
    assert plan_actions(store,'2026-10-25') == []
    with pytest.raises(ValueError):claim_action(store,action['id'])


def test_active_delivery_lock_prevents_sending_and_recovery(store,event,tmp_path):
    from server.delivery import recover_deliveries
    ingest(store,event)
    with (tmp_path/'.delivery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert process_delivery(store,Settings(data_dir=tmp_path))['state'] == 'busy'
        assert store.one('SELECT attempts FROM deliveries')['attempts'] == 0
        with pytest.raises(ValueError,match='active'):
            recover_deliveries(store)
    assert process_delivery(store,Settings(data_dir=tmp_path))['state'] == 'blocked'


def test_failed_pdf_render_cleans_partial_file_and_reports_safe_error_class(store,event,tmp_path,monkeypatch):
    import server.documents as documents
    ingest(store,event)
    rid=store.one('SELECT id FROM receipts')['id']
    def interrupted_render(snapshot,path):
        Path(path).parent.mkdir(exist_ok=True)
        Path(path).write_bytes(b'partial document')
        raise PermissionError('sensitive-provider-detail')
    monkeypatch.setattr(documents,'render_receipt',interrupted_render)
    result=process_delivery(store,Settings(data_dir=tmp_path))
    assert result['state'] == 'failed'
    assert list((tmp_path/'receipts').glob('*.tmp')) == []
    error=store.one('SELECT error FROM deliveries')['error']
    assert 'PermissionError' in error and 'sensitive-provider-detail' not in error


@pytest.mark.parametrize('currency,imported', [('HKD',1), (None,0)])
def test_import_preserves_explicit_currency_and_holds_unknown_currency(store,tmp_path,currency,imported):
    from server.migration import import_audit
    source=tmp_path/'source';source.mkdir()
    (source/'migration-inventory.json').write_text(json.dumps({'merchantChecks':[{'merchantCode':'OTHER'}]}))
    (source/'esales-active-grace-snapshot.json').write_text(json.dumps({'merchants':[{'code':'OTHER','end':'2027-12-22','status':'active'}]}))
    (source/'billing-map.json').write_text(json.dumps([{'Name':'OTHER','Email':'other@example.test','Currency':currency}]))
    result=import_audit(store,source)
    assert result['imported'] == imported
    if imported:assert store.one("SELECT currency FROM merchants WHERE code='OTHER'")['currency'] == 'HKD'
    else:assert len(result['held']) == 1


def test_pdf_render_does_not_block_unrelated_payment_and_temp_starts_private(store,event,tmp_path,monkeypatch):
    import server.documents as documents
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    entered=threading.Event();resume=threading.Event();render=documents.render_receipt
    def slow_render(snapshot,path):
        entered.set()
        assert resume.wait(4)
        assert Path(path).stat().st_mode & 0o777 == 0o600
        render(snapshot,path)
    monkeypatch.setattr(documents,'render_receipt',slow_render)
    with ThreadPoolExecutor(max_workers=2) as pool:
        document=pool.submit(ensure_pdf,store,rid,tmp_path)
        assert entered.wait(2)
        event['data']['code']='second-order'
        payment=pool.submit(ingest,store,event)
        try:
            result=payment.result(timeout=1)
            assert result['state'] == 'accepted'
        finally:resume.set()
        assert document.result(timeout=3).read_bytes().startswith(b'%PDF')


@pytest.mark.parametrize('state',['uncertain','submitted'])
def test_void_refuses_unresolved_external_email_outcome(store,event,tmp_path,state):
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    with store.transaction() as c:c.execute('UPDATE deliveries SET state=?',(state,))
    client=operator(tmp_path)
    response=client.post('/api/receipts/'+rid+'/void',json={'reason':'Synthetic correction'},headers={'Origin':'http://testserver'})
    assert response.status_code == 409
    assert store.one('SELECT state FROM receipts')['state'] != 'void'


def test_voided_renewal_requires_service_reconciliation_without_reverting_esales_observation(store,event,tmp_path):
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    client=operator(tmp_path)
    response=client.post('/api/receipts/'+rid+'/void',json={'reason':'Incorrect billing document, payment remains valid'},headers={'Origin':'http://testserver'})
    assert response.status_code == 200
    assert response.json().get('service_reconciliation_required') is True
    assert store.one('SELECT service_end FROM merchants')['service_end'] == '2027-10-22'
    issue=store.one("SELECT * FROM exceptions WHERE kind='service_reconciliation'")
    assert issue and issue['resolved_at'] is None


def test_callback_for_void_receipt_is_history_without_mutating_delivery(store,event,tmp_path):
    ingest(store,event)
    with store.transaction() as c:
        c.execute("UPDATE receipts SET state='void'")
        c.execute("UPDATE deliveries SET state='submitted'")
    message=store.one('SELECT message_id FROM deliveries')['message_id']
    client=operator(tmp_path)
    response=client.post('/hooks/email',json={'message_id':message,'status':'delivered'},headers={'Authorization':'Bearer '+'y'*40})
    assert response.status_code == 200
    assert response.json().get('void') is True
    assert store.one('SELECT state FROM deliveries')['state'] == 'submitted'
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='delivery.void_callback'")['n'] == 1


@pytest.mark.parametrize('environment',['Staging','staging '])
def test_invalid_intake_environment_refuses_startup(tmp_path,environment):
    with pytest.raises(RuntimeError,match='CRM_INTAKE_ENVIRONMENT'):
        create_app(Settings(data_dir=tmp_path,demo=True,intake_environment=environment))


def test_lead_to_due_transition_keeps_only_one_claimable_reminder(store):
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    store.save_merchant({'code':'SAMPLE','phone':'+85360000000'},'Synthetic verified number')
    lead=plan_actions(store,'2026-10-20')[0]
    due=plan_actions(store,'2026-10-22')
    assert len(due) == 1 and due[0]['id'] != lead['id']
    with pytest.raises(ValueError):claim_action(store,lead['id'])


def test_corrected_service_date_cancels_old_queued_action_immediately(store):
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    action=plan_actions(store,'2026-10-25')[0]
    store.save_merchant({'code':'SAMPLE','service_end':'2028-10-22'},'Verified actual service date')
    assert store.one('SELECT state FROM actions WHERE id=?',(action['id'],))['state'] == 'cancelled'
    assert plan_actions(store,'2026-10-25') == []


def test_missing_service_end_is_reported_once_when_automation_enabled(store):
    store.save_merchant({'code':'SAMPLE','service_end':None},'Date awaiting confirmation')
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    assert plan_actions(store,'2026-10-25') == []
    assert plan_actions(store,'2026-10-25') == []
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='merchant_data'")['n'] == 1


@pytest.mark.parametrize('reason',['RECONCILED:','RECONCILED:   '])
def test_uncertain_retry_requires_actual_reconciliation_detail(store,event,reason):
    from server.delivery import retry_delivery
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    with store.transaction() as c:c.execute("UPDATE deliveries SET state='uncertain',attempts=1")
    with pytest.raises(ValueError):retry_delivery(store,rid,reason)
    assert store.one('SELECT state FROM deliveries')['state'] == 'uncertain'


def test_login_attempts_are_audited_without_submitted_passwords(store,tmp_path):
    client=operator(tmp_path)
    secret='DO NOT LOG THIS SYNTHETIC VALUE'
    for _ in range(9):client.post('/api/login',json={'password':secret},headers={'Origin':'http://testserver'})
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='auth.login_failed'")['n'] == 8
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='auth.login_limited'")['n'] == 1
    assert secret not in json.dumps(store.all('SELECT * FROM events'))


def test_releasing_original_order_preserves_a_separate_conflicting_retry_for_review(store,event):
    import copy
    from server.orders import release
    store.save_merchant({'code':'SAMPLE','email':''},'Billing identity awaiting review')
    original=ingest(store,event)
    changed=copy.deepcopy(event)
    changed['data']['items'][0]['quantity']=6
    changed['data'].update(price=8400,payment_price=7000)
    assert ingest(store,changed)['state'] == 'conflict'
    assert store.one('SELECT state FROM orders')['state'] == 'review'
    store.save_merchant({'code':'SAMPLE','email':'verified@example.test'},'Original source and billing verified')
    release(store,original['order_id'],'Verified original completed order; conflicting retry rejected')
    assert store.one('SELECT total_minor FROM orders')['total_minor'] == 1540000
    assert store.one('SELECT COUNT(*) n FROM receipts')['n'] == 1
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='conflict' AND resolved_at IS NULL")['n'] == 1
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='review' AND resolved_at IS NULL")['n'] == 0


def test_conflicting_retry_can_be_reconciled_without_releasing_the_held_payment(store,event,tmp_path):
    import copy
    store.save_merchant({'code':'SAMPLE','email':''},'Billing identity awaiting review')
    original=ingest(store,event)
    changed=copy.deepcopy(event)
    changed['data']['items'][0]['quantity']=6
    changed['data'].update(price=8400,payment_price=7000)
    ingest(store,changed)
    conflict=store.one("SELECT id FROM exceptions WHERE kind='conflict'")['id']
    payment=store.one("SELECT id FROM exceptions WHERE kind='review'")['id']
    client=operator(tmp_path);headers={'Origin':'http://testserver'}
    assert client.post('/api/exceptions/'+conflict+'/resolve',json={'reason':'Verified original paid amount; received retry was incorrect'},headers=headers).status_code == 200
    assert client.post('/api/exceptions/'+payment+'/resolve',json={'reason':'Attempt to bypass payment release'},headers=headers).status_code == 422
    assert store.one('SELECT state FROM orders WHERE id=?',(original['order_id'],))['state'] == 'review'
    assert store.one('SELECT COUNT(*) n FROM receipts')['n'] == 0


def test_repeated_void_is_refused_without_duplicate_audit(store,event,tmp_path):
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id'];client=operator(tmp_path)
    headers={'Origin':'http://testserver'}
    assert client.post('/api/receipts/'+rid+'/void',json={'reason':'   '},headers=headers).status_code == 422
    assert client.post('/api/receipts/'+rid+'/void',json={'reason':'Verified document error'},headers=headers).status_code == 200
    assert client.post('/api/receipts/'+rid+'/void',json={'reason':'Second attempt'},headers=headers).status_code == 409
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='receipt.void'")['n'] == 1


def test_void_of_unsupported_historical_marker_is_explicit_not_missing(store,event,tmp_path):
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    with store.transaction() as c:
        c.execute('DELETE FROM deliveries')
        c.execute("UPDATE receipts SET state='legacy'")
    response=operator(tmp_path).post('/api/receipts/'+rid+'/void',json={'reason':'Attempt on unsupported historical record'},headers={'Origin':'http://testserver'})
    assert response.status_code == 409
    assert 'historical' in response.json()['detail'].lower()


def failed_action(store,status='failed'):
    from server.automation import finish_action
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    action=plan_actions(store,'2026-10-25')[0]
    claim_action(store,action['id']);finish_action(store,action['id'],status,None)
    return action


def test_operator_can_requeue_confirmed_failed_action_without_external_effect(store):
    from server.automation import requeue_action
    action=failed_action(store)
    requeue_action(store,action['id'],'Verified failed API call made no external change')
    assert store.one('SELECT state FROM actions')['state'] == 'queued'
    assert store.one('SELECT access_status FROM merchants')['access_status'] != 'inactive'
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='automation.retry'")['n'] == 1
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='automation' AND resolved_at IS NULL")['n'] == 0


def test_uncertain_action_retry_needs_reconciliation_evidence(store):
    from server.automation import requeue_action
    action=failed_action(store,'uncertain')
    with pytest.raises(ValueError):requeue_action(store,action['id'],'Please retry')
    with pytest.raises(ValueError):requeue_action(store,action['id'],'RECONCILED:  ')
    requeue_action(store,action['id'],'RECONCILED: Make stopped before the external module; verified no effect')
    assert store.one('SELECT state FROM actions')['state'] == 'queued'


@pytest.mark.parametrize('change',['date','disabled'])
def test_action_retry_refuses_superseded_or_disabled_policy(store,change):
    from server.automation import requeue_action
    action=failed_action(store)
    if change=='date':store.save_merchant({'code':'SAMPLE','service_end':'2028-10-22'},'Verified renewed term')
    else:save_policy(store,{**DEFAULT,'enabled':False})
    with pytest.raises(ValueError):requeue_action(store,action['id'],'Verified old attempt failed')
    assert store.one('SELECT state FROM actions')['state'] == 'failed'


def test_make_intake_key_cannot_requeue_an_operator_held_action(store,tmp_path):
    action=failed_action(store);client=operator(tmp_path)
    anonymous=TestClient(client.app)
    path='/api/actions/'+action['id']+'/retry'
    assert anonymous.post(path,json={'reason':'Verified failure'},headers={'Authorization':'Bearer '+'x'*40,'Origin':'http://testserver'}).status_code == 401
    assert client.post(path,json={'reason':'Verified no external effect'},headers={'Origin':'http://testserver'}).status_code == 200


def test_requeued_action_survives_planning_and_can_be_claimed(store):
    from server.automation import requeue_action
    action=failed_action(store)
    requeue_action(store,action['id'],'Verified no external effect')
    planned=plan_actions(store,'2026-10-25')
    assert [row['id'] for row in planned] == [action['id']]
    assert [row['id'] for row in plan_actions(store,'2026-10-25')] == [action['id']]
    assert claim_action(store,action['id'])['id'] == action['id']


def test_receipt_prefix_lock_is_enforced_by_operator_api(store,event,tmp_path):
    ingest(store,event);client=operator(tmp_path)
    response=client.put('/api/settings',json={'issuer':{'name':'Sample issuer','address':'Macau','email':'billing@example.test'},'prefix':'CHANGED-','issue_enabled':True},headers={'Origin':'http://testserver'})
    assert response.status_code in (409,422)
    assert store.one('SELECT prefix FROM businesses')['prefix'] == 'TEST-2026-'


@pytest.mark.parametrize('issuer',[None, [], 'invalid', {'name':'Sample','address':'Macau'}])
def test_malformed_issuer_is_rejected_without_changing_settings(store,tmp_path,issuer):
    before=store.one('SELECT issuer FROM businesses')['issuer']
    response=operator(tmp_path).put('/api/settings',json={'issuer':issuer,'prefix':'TEST-2026-','issue_enabled':True},headers={'Origin':'http://testserver'})
    assert response.status_code == 422
    assert store.one('SELECT issuer FROM businesses')['issuer'] == before


@pytest.mark.parametrize('state',['submitted','delivered','uncertain'])
def test_another_email_copy_requires_explicit_confirmation(store,event,state):
    from server.delivery import retry_delivery
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    with store.transaction() as c:c.execute('UPDATE deliveries SET state=?,attempts=1',(state,))
    note='RECONCILED: The test recipient requested another copy, acknowledging possible earlier delivery'
    with pytest.raises(ValueError):retry_delivery(store,rid,note)
    retry_delivery(store,rid,note,allow_duplicate_copy=True)
    assert store.one('SELECT state FROM deliveries')['state'] == 'queued'
    assert store.one('SELECT COUNT(*) n FROM receipts')['n'] == 1


def test_unknown_old_email_id_is_not_accepted_as_issued_history(store,event,tmp_path):
    ingest(store,event);rid=store.one('SELECT id FROM receipts')['id']
    with store.transaction() as c:c.execute("UPDATE deliveries SET state='submitted',attempts=2,message_id=?",(f'<{rid}.2@esales-receipts.local>',))
    client=operator(tmp_path)
    response=client.post('/hooks/email',json={'message_id':f'<{rid}.1@esales-receipts.local>','status':'bounced'},headers={'Authorization':'Bearer '+'y'*40})
    assert response.status_code == 404
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='delivery.historical_callback'")['n'] == 0


def test_authenticated_provider_result_can_resolve_uncertain_current_attempt(store,event,tmp_path):
    ingest(store,event)
    process_delivery(store,Settings(data_dir=tmp_path))
    with store.transaction() as c:c.execute("UPDATE deliveries SET state='uncertain'")
    message=store.one('SELECT message_id FROM deliveries')['message_id']
    response=operator(tmp_path).post('/hooks/email',json={'message_id':message,'status':'delivered'},headers={'Authorization':'Bearer '+'y'*40})
    assert response.status_code == 200
    assert store.one('SELECT state FROM deliveries')['state'] == 'delivered'
    assert store.one('SELECT resolved_at FROM exceptions')['resolved_at'] is not None


def test_conflict_retains_received_amount_for_reconciliation(store,event):
    import copy
    ingest(store,event);changed=copy.deepcopy(event)
    changed['data']['items'][0]['quantity']=6
    changed['data'].update(price=8400,payment_price=7000)
    ingest(store,changed)
    detail=store.one("SELECT detail FROM exceptions WHERE kind='conflict'")['detail']
    assert '15400.00' in detail and '7000.00' in detail
    evidence=json.loads(store.one("SELECT detail FROM events WHERE action='order.conflict'")['detail'])
    assert evidence['received']['total_minor'] == 700000
    assert store.one('SELECT total_minor FROM orders')['total_minor'] == 1540000


def test_concurrent_void_and_sender_never_send_a_void_document(store,event,tmp_path):
    import socket
    from aiosmtpd.controller import Controller
    class Sink:
        def __init__(self):self.messages=[]
        async def handle_DATA(self,server,session,envelope):
            self.messages.append(envelope.content)
            return '250 accepted'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    sink=Sink();controller=Controller(sink,hostname='127.0.0.1',port=port);controller.start()
    try:
        ingest(store,event);rid=store.one('SELECT id FROM receipts')['id'];client=operator(tmp_path)
        settings=Settings(data_dir=tmp_path,smtp_host='127.0.0.1',smtp_port=port,smtp_tls=False,smtp_from='billing@example.test',delivery_enabled=True)
        start=threading.Barrier(2)
        def void():
            start.wait(timeout=3)
            return client.post('/api/receipts/'+rid+'/void',json={'reason':'Synthetic concurrent correction'},headers={'Origin':'http://testserver'})
        def send():
            start.wait(timeout=3)
            return process_delivery(store,settings)
        with ThreadPoolExecutor(max_workers=2) as pool:
            voided=pool.submit(void);sent=pool.submit(send)
            response=voided.result(timeout=10);outcome=sent.result(timeout=10)
        if response.status_code==200:
            assert store.one('SELECT state FROM receipts')['state'] == 'void'
            assert outcome['state']=='idle' and not sink.messages
        else:
            assert response.status_code==409
            assert store.one('SELECT state FROM receipts')['state'] != 'void'
            assert outcome['state']=='submitted' and len(sink.messages)==1
    finally:controller.stop()


def test_invalid_password_request_never_echoes_the_submitted_value(tmp_path):
    client=operator(tmp_path)
    secret='SYNTHETIC-DO-NOT-ECHO-'*20
    response=client.post('/api/login',json={'password':secret},headers={'Origin':'http://testserver'})
    assert response.status_code==422
    assert secret not in response.text


def test_worker_recovers_a_crashed_claim_to_visible_uncertainty_without_resending(store,event,tmp_path):
    ingest(store,event)
    with store.transaction() as c:
        c.execute("UPDATE deliveries SET state='processing',attempts=1,claimed_at='2026-01-01T00:00:00+00:00'")
    assert process_delivery(store,Settings(data_dir=tmp_path))['state'] == 'idle'
    job=store.one('SELECT state,attempts FROM deliveries')
    assert job == {'state':'uncertain','attempts':1}
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='delivery' AND resolved_at IS NULL")['n'] == 1


def test_overview_supports_current_merchants_with_no_payments(store,tmp_path):
    response=operator(tmp_path).get('/api/overview')
    assert response.status_code == 200
    assert response.json()['orders'] == 0
    assert response.json()['last_event'] is None


def test_non_ascii_intake_credentials_are_refused_without_a_server_error(store,event,tmp_path):
    response=operator(tmp_path).post('/hooks/esales',json=event,
        headers=[(b'Authorization',b'Bearer \xff')])
    assert response.status_code == 401
    assert store.one('SELECT COUNT(*) n FROM orders')['n'] == 0


@pytest.mark.parametrize('message_id',[{},[],123,None])
def test_malformed_email_callback_identity_is_rejected_without_database_work(tmp_path,message_id):
    client=operator(tmp_path)
    response=client.post('/hooks/email',json={'message_id':message_id,'status':'bounced'},
        headers={'Authorization':'Bearer '+'y'*40})
    assert response.status_code == 422


def test_simultaneous_new_merchant_requests_cannot_overwrite_each_other(tmp_path,monkeypatch):
    client=operator(tmp_path);db=client.app.state.store
    barrier=threading.Barrier(2);read=db.one
    def simultaneous_check(query,params=()):
        result=read(query,params)
        if query=='SELECT id FROM merchants WHERE code=?' and params==('NEW',):barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(db,'one',simultaneous_check)
    def create(name):
        return client.post('/api/merchants',json={'code':'NEW','name':name},headers={'Origin':'http://testserver'})
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(create,['First business','Second business']))
    assert sorted(response.status_code for response in responses) == [200,409]
    winner=next(response.json()['id'] for response in responses if response.status_code==200)
    assert read("SELECT COUNT(*) n FROM merchants WHERE code='NEW'")['n'] == 1
    assert read("SELECT id FROM merchants WHERE code='NEW'")['id'] == winner


@pytest.mark.parametrize('phone',['','+85398765432'])
def test_editing_a_reminder_phone_cancels_the_old_queued_recipient(store,phone):
    store.save_merchant({'code':'SAMPLE','phone':'+85312345678'},'Verified SMS recipient')
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    action=plan_actions(store,'2026-10-21')[0]
    store.save_merchant({'code':'SAMPLE','phone':phone},'Old phone was incorrect')
    assert store.one('SELECT state FROM actions WHERE id=?',(action['id'],))['state'] == 'cancelled'
    assert plan_actions(store,'2026-10-21') == []
    with pytest.raises(ValueError):claim_action(store,action['id'])


def test_claim_refuses_a_legacy_reminder_with_a_stale_phone_snapshot(store):
    store.save_merchant({'code':'SAMPLE','phone':'+85312345678'},'Verified SMS recipient')
    save_policy(store,{**DEFAULT,'enabled':True,'approved':True})
    action=plan_actions(store,'2026-10-21')[0]
    with store.transaction() as c:c.execute("UPDATE merchants SET phone='' WHERE code='SAMPLE'")
    with pytest.raises(ValueError):claim_action(store,action['id'])
    assert store.one('SELECT state FROM actions WHERE id=?',(action['id'],))['state'] != 'processing'


def test_reconciling_an_issue_twice_preserves_the_original_decision(store,event,tmp_path):
    import copy
    ingest(store,event);changed=copy.deepcopy(event)
    changed['data']['items'][0]['quantity']=6;changed['data'].update(price=8400,payment_price=7000)
    ingest(store,changed)
    issue=store.one("SELECT id FROM exceptions WHERE kind='conflict'")['id']
    client=operator(tmp_path);headers={'Origin':'http://testserver'}
    assert client.post('/api/exceptions/'+issue+'/resolve',json={'reason':'Original source checked and conflict reconciled'},headers=headers).status_code == 200
    first=store.one('SELECT resolved_at,resolution FROM exceptions WHERE id=?',(issue,))
    assert client.post('/api/exceptions/'+issue+'/resolve',json={'reason':'Second attempt to overwrite the decision'},headers=headers).status_code == 409
    assert store.one('SELECT resolved_at,resolution FROM exceptions WHERE id=?',(issue,)) == first
    assert store.one("SELECT COUNT(*) n FROM events WHERE action='exception.resolved'")['n'] == 1
