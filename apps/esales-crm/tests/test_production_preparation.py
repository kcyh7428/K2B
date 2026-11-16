import copy
import json
import pytest

from server.automation import DEFAULT, claim_action, plan_actions, requeue_action, save_policy, get_policy, finish_action
from server.db import Store, now, VersionConflict
from server.orders import ingest
from test_api import client, login


def enable_reminders(store):
    store.save_merchant({'code': 'SAMPLE', 'phone': '+85360000000'}, 'Verified test contact')
    save_policy(store, {**DEFAULT, 'enabled': True, 'approved': True, 'lead_days': 1})


def topup(event):
    payload = copy.deepcopy(event)
    payload['data'].update(price=400, payment_price=400, coupon_discount=0)
    payload['data'].pop('merchant_subscription_end_date')
    payload['data']['items'] = [{'system_product_type': 'etoken', 'system_product_name': 'SMS tokens', 'quantity': 1, 'unit_price': 400}]
    return payload


def test_web_billing_exclusion_blocks_receipts_and_reminders(store, event):
    enable_reminders(store)
    queued = plan_actions(store, '2026-10-21')[0]
    store.save_merchant({'code': 'SAMPLE', 'web_billing_enabled': False}, 'Internal-use merchant')
    assert store.one('SELECT state FROM actions WHERE id=?', (queued['id'],))['state'] == 'cancelled'
    assert store.one("SELECT count(*) n FROM events WHERE action='automation.cancelled' AND subject=?", (queued['id'],))['n'] == 1
    assert plan_actions(store, '2026-10-22') == []
    assert ingest(store, event)['state'] == 'review'
    assert store.one('SELECT count(*) n FROM receipts')['n'] == 0
    assert store.one('SELECT count(*) n FROM deliveries')['n'] == 0
    with pytest.raises(ValueError):
        claim_action(store, queued['id'])
    with pytest.raises(ValueError):
        requeue_action(store, queued['id'], 'Attempt to retry an excluded merchant')


def test_grace_renewal_has_one_manual_reactivation_task(store, event):
    store.save_merchant({'code': 'SAMPLE', 'access_status': 'grace'}, 'Observed e-sales grace period')
    assert ingest(store, event)['state'] == 'accepted'
    assert ingest(store, event)['duplicate'] is True
    assert store.one('SELECT count(*) n FROM receipts')['n'] == 1
    assert store.one("SELECT count(*) n FROM exceptions WHERE kind='access'")['n'] == 1
    assert store.one('SELECT access_status FROM merchants')['access_status'] == 'grace'


def test_exact_reminder_days_do_not_create_catchup_or_suspension(store):
    enable_reminders(store)
    assert plan_actions(store, '2026-10-20') == []
    assert len(plan_actions(store, '2026-10-21')) == 1
    assert len(plan_actions(store, '2026-10-22')) == 1
    assert plan_actions(store, '2026-10-23') == []
    assert plan_actions(store, '2026-10-25') == []
    assert store.one("SELECT count(*) n FROM actions WHERE kind='suspend'")['n'] == 0


def test_old_queued_suspension_cannot_be_claimed_or_retried(store):
    enable_reminders(store)
    with store.transaction() as c:
        c.execute("INSERT INTO actions(id,merchant_code,service_end,kind,payload,state,created_at) VALUES(?,?,?,?,?,'queued',?)",
                  ('old-suspension', 'SAMPLE', '2026-10-22', 'suspend', '{}', now()))
    with pytest.raises(ValueError):
        claim_action(store, 'old-suspension')
    assert plan_actions(store, '2026-10-25') == []
    assert store.one("SELECT state FROM actions WHERE id='old-suspension'")['state'] == 'cancelled'
    with pytest.raises(ValueError):
        requeue_action(store, 'old-suspension', 'Attempt to restart an unsupported suspension')


def test_existing_database_gains_web_billing_flag(store):
    columns = {r['name'] for r in store.all('PRAGMA table_info(merchants)')}
    if 'web_billing_enabled' in columns:
        with store.transaction() as c:
            c.execute('ALTER TABLE merchants DROP COLUMN web_billing_enabled')
    migrated = Store(store.path)
    assert migrated.one('SELECT * FROM merchants')['web_billing_enabled'] == 1


def test_older_operator_payload_preserves_web_billing_exclusion(store, tmp_path):
    c = client(tmp_path)
    login(c)
    origin = {'Origin': 'http://testserver'}
    row = c.get('/api/merchants').json()[0]
    data = {k: row[k] for k in ['code', 'name', 'service_end', 'access_status']}
    data.update(web_billing_enabled=False, expected_updated_at=row['updated_at'])
    assert c.put('/api/merchants/' + row['id'], json=data, headers=origin).status_code == 200
    row = c.get('/api/merchants').json()[0]
    data.pop('web_billing_enabled')
    data['expected_updated_at'] = row['updated_at']
    assert c.put('/api/merchants/' + row['id'], json=data, headers=origin).status_code == 200
    assert c.get('/api/merchants').json()[0]['web_billing_enabled'] == 0


def test_record_existing_topup_preserves_receipt_status_on_webhook_replay(store, event):
    payload = topup(event)
    evidence = {'source': 'QuickBooks', 'reference': '2801', 'purchased_on': '2026-11-16'}
    result = ingest(store, payload, external_receipt=evidence, manual_reason='Verified existing receipt')
    assert result['state'] == 'recorded_elsewhere'
    assert ingest(store, payload)['duplicate'] is True
    assert store.one('SELECT count(*) n FROM receipts')['n'] == 0
    assert store.one('SELECT count(*) n FROM deliveries')['n'] == 0
    assert store.one('SELECT service_end FROM merchants')['service_end'] == '2026-10-22'
    recorded = json.loads(store.one('SELECT payload FROM orders')['payload'])
    assert recorded['external_receipt'] == evidence
    changed = copy.deepcopy(payload)
    changed['data'].update(price=800, payment_price=800)
    changed['data']['items'][0]['quantity'] = 2
    assert ingest(store, changed)['state'] == 'conflict'
    assert store.one('SELECT count(*) n FROM receipts')['n'] == 0


def test_existing_topup_endpoint_requires_operator_and_receipt_confirmation(store, event, tmp_path):
    c = client(tmp_path)
    payload = topup(event)
    body = {'payload': payload, 'already_receipted': True, 'external_receipt': {'source': 'Keith confirmation', 'reference': '', 'purchased_on': '2026-11-11'}, 'reason': 'Confirmed receipted elsewhere'}
    path = '/api/orders/record-existing-topup'
    origin = {'Origin': 'http://testserver'}
    assert c.post(path, json=body, headers=origin).status_code == 401
    login(c)
    missing_confirmation = {**body, 'already_receipted': False}
    assert c.post(path, json=missing_confirmation, headers=origin).status_code == 422
    response = c.post(path, json=body, headers=origin)
    assert response.status_code == 200
    assert response.json()['state'] == 'recorded_elsewhere'
    assert c.get('/api/orders').json()[0]['external_receipt']['source'] == 'Keith confirmation'
    assert store.one('SELECT count(*) n FROM deliveries')['n'] == 0


@pytest.mark.parametrize('planned_on,claimed_on', [('2026-10-21', '2026-10-22'), ('2026-10-22', '2026-10-23')])
def test_claim_and_retry_require_the_original_exact_reminder_day(store, automation_clock, planned_on, claimed_on):
    enable_reminders(store)
    automation_clock(planned_on)
    action = plan_actions(store)[0]
    automation_clock(claimed_on)
    with pytest.raises(ValueError, match='scheduled day'):
        claim_action(store, action['id'])
    with store.transaction() as c:
        c.execute("UPDATE actions SET state='failed' WHERE id=?", (action['id'],))
    with pytest.raises(ValueError, match='scheduled day'):
        requeue_action(store, action['id'], 'Verified previous attempt failed')
    assert store.one('SELECT state FROM actions')['state'] == 'failed'


def test_unverified_stored_two_day_policy_requires_new_approval(store):
    with store.transaction() as c:
        c.execute("INSERT INTO metadata VALUES('automation_policy', ?)", (json.dumps({**DEFAULT, 'enabled': True, 'approved': True, 'lead_days': 2}),))
    assert get_policy(store) == {**DEFAULT, 'lead_days': 1, 'enabled': False, 'approved': False}
    assert plan_actions(store, '2026-10-20') == []
    assert plan_actions(store, '2026-10-20') == []
    assert store.one("SELECT count(*) n FROM events WHERE action='automation.policy_invalid'")['n'] == 1
    with pytest.raises(ValueError):
        save_policy(store, {**DEFAULT, 'enabled': True, 'approved': True, 'lead_days': 2})


def test_differing_existing_receipt_evidence_is_not_silently_discarded(store, event):
    payload = topup(event)
    evidence = {'source': 'Keith confirmation', 'reference': '', 'purchased_on': '2026-11-11'}
    ingest(store, payload, external_receipt=evidence, manual_reason='Confirmed already receipted')
    with pytest.raises(VersionConflict, match='evidence differs'):
        ingest(store, payload, external_receipt={**evidence, 'reference': 'new-number'}, manual_reason='New reference supplied')
    assert json.loads(store.one('SELECT payload FROM orders')['payload'])['external_receipt'] == evidence


def test_renewal_hook_honors_web_billing_exclusion(tmp_path):
    c = client(tmp_path)
    c.app.state.store.save_merchant({'code': 'SAMPLE', 'web_billing_enabled': False}, 'Internal-use account')
    response = c.get('/hooks/merchants/SAMPLE/renewal', headers={'Authorization': 'Bearer ' + 'x' * 40})
    assert response.status_code == 200
    assert response.json()['renewal_state'] == 'excluded'


def test_malformed_old_payload_does_not_hide_the_orders_list(tmp_path, event):
    c = client(tmp_path)
    db = c.app.state.store
    ingest(db, topup(event), environment='production')
    with db.transaction() as sql:
        sql.execute("UPDATE orders SET payload='broken legacy row'")
    login(c)
    response = c.get('/api/orders')
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]['external_receipt'] is None


def test_planning_during_a_claim_preserves_processing_and_single_use(store):
    enable_reminders(store)
    action = plan_actions(store)[0]
    claim_action(store, action['id'])
    before = store.one('SELECT * FROM actions WHERE id=?', (action['id'],))
    assert plan_actions(store) == []
    assert store.one('SELECT * FROM actions WHERE id=?', (action['id'],)) == before
    with pytest.raises(ValueError):
        claim_action(store, action['id'])
    assert finish_action(store, action['id'], 'submitted', None)['state'] == 'submitted'
    assert plan_actions(store) == []
    assert store.one('SELECT count(*) n FROM actions')['n'] == 1


@pytest.mark.parametrize('state', ['failed', 'uncertain', 'submitted'])
def test_planning_preserves_action_outcomes_and_history(store, automation_clock, state):
    enable_reminders(store)
    action = plan_actions(store)[0]
    claim_action(store, action['id'])
    finish_action(store, action['id'], state, None)
    before = store.one('SELECT * FROM actions WHERE id=?', (action['id'],))
    assert plan_actions(store) == []
    assert store.one('SELECT * FROM actions WHERE id=?', (action['id'],)) == before
    automation_clock('2026-10-23')
    assert plan_actions(store) == []
    assert store.one('SELECT * FROM actions WHERE id=?', (action['id'],)) == before
    assert store.one('SELECT count(*) n FROM actions')['n'] == 1


def test_separate_expiry_reminder_is_allowed_without_replaying_uncertain_advance(store, automation_clock):
    enable_reminders(store)
    automation_clock('2026-10-21')
    lead = plan_actions(store)[0]
    claim_action(store, lead['id'])
    with store.transaction() as c:
        c.execute("UPDATE actions SET claimed_at='2026-10-21T04:00:00+00:00' WHERE id=?", (lead['id'],))
    automation_clock('2026-10-22')
    due = plan_actions(store)
    assert len(due) == 1 and due[0]['id'] != lead['id']
    assert json.loads(due[0]['payload'])['text'] != json.loads(lead['payload'])['text']
    assert store.one('SELECT state FROM actions WHERE id=?', (lead['id'],))['state'] == 'uncertain'
    with pytest.raises(ValueError):
        claim_action(store, lead['id'])
    claim_action(store, due[0]['id'])
    assert plan_actions(store) == []
    assert finish_action(store, due[0]['id'], 'submitted', None)['state'] == 'submitted'
    assert store.one('SELECT count(*) n FROM actions')['n'] == 2


def test_renewal_during_sms_records_timestamp_and_audit_for_review(store, event):
    enable_reminders(store)
    action = plan_actions(store)[0]
    claim_action(store, action['id'])
    ingest(store, event)
    assert finish_action(store, action['id'], 'submitted', None)['state'] == 'uncertain'
    assert store.one('SELECT finished_at FROM actions')['finished_at']
    assert store.one("SELECT count(*) n FROM events WHERE action='automation.renewal_race'")['n'] == 1
    assert store.one('SELECT service_end FROM merchants')['service_end'] == '2027-10-22'


@pytest.mark.parametrize('replan', [False, True])
def test_claim_uses_current_approved_sms_text_after_template_edit(store, replan):
    enable_reminders(store)
    action = plan_actions(store)[0]
    save_policy(store, {**get_policy(store), 'due_template': 'Corrected expiry for {merchant} on {service_end}'})
    if replan:
        assert json.loads(plan_actions(store)[0]['payload'])['text'] == 'Corrected expiry for SAMPLE on 2026-10-22'
    assert claim_action(store, action['id'])['payload']['text'] == 'Corrected expiry for SAMPLE on 2026-10-22'


def test_retry_refreshes_the_approved_sms_text(store):
    enable_reminders(store)
    action = plan_actions(store)[0]
    claim_action(store, action['id'])
    finish_action(store, action['id'], 'failed', None)
    save_policy(store, {**get_policy(store), 'due_template': 'Corrected expiry for {merchant}'})
    requeue_action(store, action['id'], 'Confirmed failed before SMS submission')
    assert json.loads(store.one('SELECT payload FROM actions')['payload'])['text'] == 'Corrected expiry for SAMPLE'


def test_unclaimed_reminder_cancellation_is_audited_once_for_each_slot(store, automation_clock):
    enable_reminders(store)
    automation_clock('2026-10-21')
    lead = plan_actions(store)[0]
    automation_clock('2026-10-22')
    due = plan_actions(store)[0]
    assert due['id'] != lead['id']
    automation_clock('2026-10-23')
    assert plan_actions(store) == []
    assert plan_actions(store) == []
    events = store.all("SELECT subject,detail FROM events WHERE action='automation.cancelled'")
    assert {row['subject'] for row in events} == {lead['id'], due['id']}
    assert len(events) == 2
    assert all('not claimed' in row['detail'] for row in events)
    assert store.one('SELECT count(*) n FROM actions')['n'] == 2


def test_disabling_policy_audits_each_queued_cancellation(store):
    enable_reminders(store)
    action = plan_actions(store)[0]
    save_policy(store, {**get_policy(store), 'enabled': False})
    save_policy(store, {**get_policy(store), 'enabled': False})
    assert store.one("SELECT count(*) n FROM events WHERE action='automation.cancelled' AND subject=?", (action['id'],))['n'] == 1


@pytest.mark.parametrize('raw', ['broken-json', '[]', '{"lead_days":1}', json.dumps({**DEFAULT, 'enabled': True, 'approved': True, 'due_template': '{unsupported}'})])
def test_malformed_stored_policy_keeps_automation_disabled(store, raw):
    with store.transaction() as c:
        c.execute("INSERT INTO metadata VALUES('automation_policy', ?)", (raw,))
    assert get_policy(store) == DEFAULT
    assert plan_actions(store) == []
    assert plan_actions(store) == []
    assert store.one("SELECT count(*) n FROM exceptions WHERE kind='automation' AND detail LIKE 'Invalid reminder policy%'")['n'] == 1
    assert store.one("SELECT count(*) n FROM events WHERE action='automation.policy_invalid'")['n'] == 1
    save_policy(store, dict(DEFAULT))
    assert store.one("SELECT resolved_at FROM exceptions WHERE kind='automation' AND detail LIKE 'Invalid reminder policy%'")['resolved_at']


def test_policy_edit_is_serialized_with_the_claim_snapshot(store, monkeypatch):
    import server.automation as automation
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    enable_reminders(store)
    action = plan_actions(store)[0]
    policy = get_policy(store)
    edited = {**policy, 'due_template': 'New text for the next unclaimed message'}
    original = automation.get_policy
    attempted, committed = Event(), Event()
    future = []
    def writer():
        attempted.set()
        save_policy(store, edited)
        committed.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        def read_during_claim(db, connection=None):
            future.append(pool.submit(writer))
            assert attempted.wait(2)
            assert not committed.wait(0.05)
            return original(db, connection) if connection is not None else original(db)
        monkeypatch.setattr(automation, 'get_policy', read_during_claim)
        claimed = claim_action(store, action['id'])
        future[0].result(timeout=5)
    assert committed.is_set()
    assert claimed['payload']['text'] == policy['due_template'].format(merchant='SAMPLE', service_end='2026-10-22')
    assert original(store)['due_template'] == edited['due_template']


def test_cancelled_eligible_reminder_requires_visible_review_before_same_day_retry(store):
    enable_reminders(store)
    action = plan_actions(store)[0]
    store.save_merchant({'code': 'SAMPLE', 'phone': '+85361111111'}, 'Corrected verified contact')
    assert plan_actions(store) == []
    assert plan_actions(store) == []
    issue = store.one("SELECT * FROM exceptions WHERE kind='automation' AND detail LIKE ?", ('%' + action['id'] + '%',))
    assert issue and issue['resolved_at'] is None
    assert store.one("SELECT count(*) n FROM events WHERE action='automation.review_required' AND subject=?", (action['id'],))['n'] == 1
    requeue_action(store, action['id'], 'Reviewed corrected contact before any SMS submission')
    assert claim_action(store, action['id'])['payload']['phone'] == '+85361111111'
    assert store.one('SELECT resolved_at FROM exceptions WHERE id=?', (issue['id'],))['resolved_at']
