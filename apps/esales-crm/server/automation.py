import hashlib
import json
import re
from datetime import date,datetime,timezone,timedelta
from string import Formatter
from zoneinfo import ZoneInfo
from server.db import now
from server.orders import hold,text


DEFAULT={'enabled':False,'approved':False,'lead_days':2,'grace_days':3,'lead_template':'Your e-sales service period ends {service_end}. Please review the payment notice in your e-sales portal.','due_template':'Your e-sales service period ended {service_end}. Please review the payment notice in your e-sales portal.'}


def get_policy(store):
    row=store.one("SELECT value FROM metadata WHERE key='automation_policy'")
    return json.loads(row['value']) if row else dict(DEFAULT)


def save_policy(store,policy):
    if not isinstance(policy,dict) or set(policy)!=set(DEFAULT):raise ValueError('Supply the complete reminder policy')
    if type(policy['enabled']) is not bool or type(policy['approved']) is not bool:raise ValueError('Explicit approval flags required')
    for key in ('lead_days','grace_days'):
        if type(policy[key]) is not int or not 1<=policy[key]<=30:raise ValueError('Timing must be between 1 and 30 days')
    for key in ('lead_template','due_template'):
        if not isinstance(policy[key],str) or not 1<=len(policy[key])<=600:raise ValueError('SMS text must be 1 to 600 characters')
        try:
            for _,name,spec,conversion in Formatter().parse(policy[key]):
                if name is not None and (name not in ('merchant','service_end') or spec or conversion):raise ValueError('Only merchant and service_end placeholders are supported')
        except ValueError:raise ValueError('Invalid SMS placeholders') from None
    if policy['enabled'] and not policy['approved']:raise ValueError('Confirm timing, SMS text and access policy before enabling')
    with store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO metadata VALUES('automation_policy',?)",(json.dumps(policy),))
        if not policy['enabled']:c.execute("UPDATE actions SET state='cancelled' WHERE state='queued'")
        store.audit(c,'automation.policy','converto','Policy approved and enabled' if policy['enabled'] else 'Automation disabled')


def plan_actions(store,today=None):
    recover_actions(store)
    policy=get_policy(store)
    if not policy['enabled'] or not policy['approved']:return []
    today=date.fromisoformat(today) if today else datetime.now(ZoneInfo('Asia/Macau')).date()
    with store.transaction() as c:
        c.execute("""UPDATE actions SET state='cancelled'
            WHERE state='queued' AND NOT EXISTS (
                SELECT 1 FROM merchants m
                WHERE m.code=actions.merchant_code
                  AND m.service_end=actions.service_end
                  AND m.access_status!='inactive'
            )""")
        for m in c.execute('SELECT * FROM merchants').fetchall():
            if m['access_status']=='inactive':continue
            try:
                if not m['service_end']:raise ValueError('Missing date')
                delta=(date.fromisoformat(m['service_end'])-today).days
            except (ValueError,TypeError):
                detail='Invalid service end date for '+m['code']+'; correct before automation'
                if not c.execute('SELECT 1 FROM exceptions WHERE detail=? AND resolved_at IS NULL',(detail,)).fetchone():hold(c,None,'merchant_data',detail)
                continue
            if delta>policy['lead_days']:continue
            slot='suspend' if delta<=-policy['grace_days'] else 'due' if delta<=0 else 'lead'
            kind='suspend' if slot=='suspend' else 'reminder'
            if slot=='suspend':c.execute("UPDATE actions SET state='cancelled' WHERE merchant_code=? AND kind='reminder' AND state='queued'",(m['code'],))
            if kind=='reminder' and not re.fullmatch(r'\+[0-9]{7,16}',m['phone'] or ''):
                # One persistent exception, not a new issue every daily poll.
                detail='Verified international SMS number required for '+m['code']
                if not c.execute('SELECT 1 FROM exceptions WHERE detail=? AND resolved_at IS NULL',(detail,)).fetchone():hold(c,None,'phone',detail)
                continue
            key=hashlib.sha256((m['code'].casefold()+'|'+m['service_end']+'|'+slot).encode()).hexdigest()
            c.execute("UPDATE actions SET state='cancelled' WHERE merchant_code=? COLLATE NOCASE AND state='queued' AND id!=?",(m['code'],key))
            payload={'merchant_code':m['code'],'service_end':m['service_end'],'status':'inactive'} if kind=='suspend' else {'merchant_code':m['code'],'service_end':m['service_end'],'phone':m['phone'],'text':policy['lead_template' if slot=='lead' else 'due_template'].format(merchant=m['name'],service_end=m['service_end'])}
            c.execute("INSERT OR IGNORE INTO actions(id,merchant_code,service_end,kind,payload,state,created_at,finished_at) VALUES(?,?,?,?,?,'queued',?,NULL)",(key,m['code'],m['service_end'],kind,json.dumps(payload),now()))
        return [dict(r) for r in c.execute("SELECT * FROM actions WHERE state='queued' ORDER BY created_at")]


def claim_action(store,key):
    if not get_policy(store)['enabled']:raise ValueError('Automation is disabled')
    with store.transaction() as c:
        row=c.execute('SELECT a.*,m.service_end current_end,m.phone current_phone,m.access_status FROM actions a JOIN merchants m ON m.code=a.merchant_code WHERE a.id=?',(key,)).fetchone()
        if not row or row['state']!='queued' or row['current_end']!=row['service_end'] or row['access_status']=='inactive':raise ValueError('Action unavailable or superseded by current merchant state')
        payload=json.loads(row['payload'])
        if row['kind']=='reminder' and (not re.fullmatch(r'\+[0-9]{7,16}',row['current_phone'] or '') or payload.get('phone')!=row['current_phone']):raise ValueError('Reminder recipient changed; review and requeue with the current verified phone')
        c.execute("UPDATE actions SET state='processing',claimed_at=? WHERE id=?",(now(),key))
        return {**dict(row),'payload':payload}


def recover_actions(store):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=5)
    with store.transaction() as c:
        count=0
        for row in c.execute("SELECT * FROM actions WHERE state='processing'").fetchall():
            try:stale=datetime.fromisoformat(row['claimed_at'] or row['created_at'])<cutoff
            except (ValueError,TypeError):stale=True
            if not stale:continue
            c.execute("UPDATE actions SET state='uncertain',finished_at=? WHERE id=?",(now(),row['id']))
            hold(c,None,'automation','Interrupted action '+row['id']+' for '+row['merchant_code']+'; verify its external outcome before any further action')
            store.audit(c,'automation.recovery',row['id'],'Stale claim held; no automatic external replay')
            count+=1
        return count


def requeue_action(store,key,reason):
    reason=text(reason,'action retry evidence',1000)
    policy=get_policy(store)
    if not policy['enabled'] or not policy['approved']:raise ValueError('Automation is disabled or unapproved')
    with store.transaction() as c:
        row=c.execute('SELECT a.*,m.service_end current_end,m.access_status,m.phone,m.name FROM actions a JOIN merchants m ON m.code=a.merchant_code WHERE a.id=?',(key,)).fetchone()
        if not row or row['state'] not in ('failed','uncertain','cancelled'):raise ValueError('Only held or cancelled actions may be requeued after review')
        if row['current_end']!=row['service_end'] or row['access_status']=='inactive':raise ValueError('Action is superseded by the current merchant state')
        if row['state']=='uncertain' and (not reason.startswith('RECONCILED:') or not reason[len('RECONCILED:'):].strip()):raise ValueError('Record verified external outcome after RECONCILED: before retry')
        payload=json.loads(row['payload'])
        if row['kind']=='reminder':
            if not re.fullmatch(r'\+[0-9]{7,16}',row['phone'] or ''):raise ValueError('Verified SMS number required')
            payload['phone']=row['phone']
        c.execute("UPDATE actions SET state='queued',claimed_at=NULL,finished_at=NULL,payload=? WHERE id=?",(json.dumps(payload),key))
        c.execute("UPDATE exceptions SET resolved_at=?,resolution=? WHERE kind='automation' AND detail LIKE ? AND resolved_at IS NULL",(now(),reason,'%'+key+'%'))
        store.audit(c,'automation.retry',key,reason)
    return {'state':'queued'}


def finish_action(store,key,status,observed):
    if status not in ('submitted','failed','uncertain'):raise ValueError('Unknown action outcome')
    with store.transaction() as c:
        row=c.execute('SELECT a.*,m.service_end current_end FROM actions a JOIN merchants m ON m.code=a.merchant_code WHERE a.id=?',(key,)).fetchone()
        if not row or row['state']!='processing':raise ValueError('Action is not in progress')
        if row['current_end']!=row['service_end']:
            hold(c,None,'access','Renewal arrived during an external action for '+row['merchant_code']+'; verify e-sales access')
            c.execute("UPDATE actions SET state='uncertain' WHERE id=?",(key,))
            return {'state':'uncertain'}
        if row['kind']=='suspend' and status=='submitted':
            if observed!='inactive':raise ValueError('Suspension requires a separate observed inactive status readback')
            c.execute("UPDATE merchants SET access_status='inactive',updated_at=? WHERE code=?",(now(),row['merchant_code']))
        c.execute('UPDATE actions SET state=?,finished_at=? WHERE id=?',(status,now(),key))
        if status in ('failed','uncertain'):hold(c,None,'automation',status+' action '+key+' for '+row['merchant_code']+'; reconcile before retry')
        store_audit=store.audit
        store_audit(c,'automation.'+status,key,'Make action result recorded; SMS submission is not handset delivery')
    return {'state':status}
