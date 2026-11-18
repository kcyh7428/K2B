from server.automation import plan_actions,claim_action,finish_action,save_policy
from server.orders import ingest
import json
import pytest


def policy(store):
    save_policy(store,{'enabled':True,'lead_days':1,'grace_days':3,'approved':True,'lead_template':'Ends {service_end}. Check e-sales.','due_template':'Service period ended {service_end}. Check e-sales.'})
    with store.transaction() as c:c.execute("UPDATE merchants SET phone='+85360000000'")


def test_no_automatic_actions_until_policy_is_approved(store):
    assert plan_actions(store,'2026-10-22')==[]


def test_reminders_use_exact_service_end_dates_and_are_idempotent(store):
    policy(store)
    first=plan_actions(store,'2026-10-21')
    assert len(first)==1 and first[0]['kind']=='reminder'
    assert len(plan_actions(store,'2026-10-21'))==1
    assert len(plan_actions(store,'2026-10-22'))==1
    assert plan_actions(store,'2026-10-25')==[]
    assert store.one("select count(*) n from actions where kind='suspend'")['n']==0


def test_renewal_invalidates_stale_access_and_reminder_actions(store,event):
    policy(store);actions=plan_actions(store,'2026-10-21')
    assert actions
    ingest(store,event)
    import pytest
    for action in actions:
        with pytest.raises(ValueError):claim_action(store,action['id'])
        assert store.one("SELECT count(*) n FROM events WHERE action='automation.cancelled' AND subject=?",(action['id'],))['n']==1


def test_legacy_processing_suspension_is_held_without_changing_access(store):
    from server.db import now
    policy(store)
    action={'id':'legacy-processing'}
    with store.transaction() as c:
        c.execute("INSERT INTO actions(id,merchant_code,service_end,kind,payload,state,created_at,claimed_at) VALUES(?,?,?,?,?,'processing',?,?)",(action['id'],'SAMPLE','2026-10-22','suspend','{}',now(),now()))
    assert finish_action(store,action['id'],'submitted','inactive')['state']=='uncertain'
    assert store.one('select access_status from merchants')['access_status']=='unknown'
    assert store.one("SELECT COUNT(*) n FROM exceptions WHERE kind='access'")['n']==1


@pytest.mark.parametrize(('service_end', 'deadline'), [
    ('2026-11-10', '11月13'),
    ('2026-11-29', '12月02'),
    ('2026-12-30', '01月02'),
    ('2028-02-27', '03月01'),
])
def test_expiry_sms_preserves_make_payment_deadline(store, automation_clock, service_end, deadline):
    from server.automation import DEFAULT
    store.save_merchant({'code': 'SAMPLE', 'service_end': service_end, 'phone': '+85360000000', 'access_status': 'grace'})
    save_policy(store, {**DEFAULT, 'enabled': True, 'approved': True, 'grace_days': 30,
                        'due_template': '{merchant}：請在 {payment_deadline_month}月{payment_deadline_day}前續費。'})
    automation_clock(service_end)
    action, = plan_actions(store, service_end)
    expected = 'SAMPLE：請在 ' + deadline + '前續費。'
    assert json.loads(action['payload'])['text'] == expected
    assert claim_action(store, action['id'])['payload']['text'] == expected
    assert store.one('SELECT access_status FROM merchants')['access_status'] == 'grace'
    assert store.one("SELECT COUNT(*) n FROM actions WHERE kind='suspend'")['n'] == 0


@pytest.mark.parametrize('placeholder', ['payment_deadline_month', 'payment_deadline_day'])
def test_payment_deadline_placeholders_are_expiry_only(store, placeholder):
    from server.automation import DEFAULT, get_policy
    with pytest.raises(ValueError, match='Invalid SMS placeholders'):
        save_policy(store, {**DEFAULT, 'lead_template': 'Pay by {' + placeholder + '}'})
    assert get_policy(store) == DEFAULT
