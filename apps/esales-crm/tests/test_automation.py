from server.automation import plan_actions,claim_action,finish_action,save_policy
from server.orders import ingest


def policy(store):
    save_policy(store,{'enabled':True,'lead_days':2,'grace_days':3,'approved':True,'lead_template':'Ends {service_end}. Check e-sales.','due_template':'Service period ended {service_end}. Check e-sales.'})
    with store.transaction() as c:c.execute("UPDATE merchants SET phone='+85360000000'")


def test_no_automatic_actions_until_policy_is_approved(store):
    assert plan_actions(store,'2026-10-22')==[]


def test_reminder_and_suspension_use_service_end_and_are_idempotent(store):
    policy(store)
    first=plan_actions(store,'2026-10-20')
    assert len(first)==1 and first[0]['kind']=='reminder'
    assert len(plan_actions(store,'2026-10-20'))==1
    assert len(plan_actions(store,'2026-10-25'))==1
    assert store.one("select count(*) n from actions where kind='suspend'")['n']==1


def test_renewal_invalidates_stale_access_and_reminder_actions(store,event):
    policy(store);actions=plan_actions(store,'2026-10-25')
    ingest(store,event)
    import pytest
    for action in actions:
        with pytest.raises(ValueError):claim_action(store,action['id'])


def test_suspension_requires_observed_status_readback(store):
    policy(store);actions=plan_actions(store,'2026-10-25')
    action=next(a for a in actions if a['kind']=='suspend')
    claim_action(store,action['id'])
    import pytest
    with pytest.raises(ValueError):finish_action(store,action['id'],'submitted',None)
    finish_action(store,action['id'],'submitted','inactive')
    assert store.one('select access_status from merchants')['access_status']=='inactive'
