from server.automation import plan_actions,claim_action,finish_action,save_policy
from server.orders import ingest


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
