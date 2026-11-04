import json
from pathlib import Path
import xlrd
from datetime import date


def import_audit(store,source):
    store.bind_mode('private')
    source=Path(source)
    checks=json.loads((source/'migration-inventory.json').read_text())['merchantChecks']
    observed=json.loads((source/'esales-active-grace-snapshot.json').read_text())['merchants']
    normalized=source/'billing-map.json'
    if normalized.exists():billing=json.loads(normalized.read_text())
    else:
        sheet=xlrd.open_workbook(source/'quickbooks-converto-customers.xls').sheet_by_index(0)
        columns=sheet.row_values(0)
        billing=[dict(zip(columns,sheet.row_values(i))) for i in range(1,sheet.nrows)]
    result={'imported':0,'skipped':0,'held':[]}
    by={row['code'].casefold():row for row in observed}
    for item in checks:
        code=item['merchantCode'];matches=[row for row in billing if str(row.get('Name','')).strip().casefold()==code.casefold()]
        state=by.get(code.casefold())
        if len(matches)!=1 or not state or not state.get('end'):
            result['held'].append({'code':code,'reason':'Missing unique billing match or observed service end'});continue
        try:end=date.fromisoformat(state['end']).isoformat()
        except (ValueError,TypeError):
            result['held'].append({'code':code,'reason':'Invalid observed service end date'});continue
        if state.get('status') not in ('active','grace','inactive','unknown'):
            result['held'].append({'code':code,'reason':'Unknown observed access status'});continue
        if store.one('SELECT id FROM merchants WHERE code=?',(code,)):
            result['skipped']+=1;continue
        customer=matches[0]
        currency=customer.get('Currency') or customer.get('currency')
        if currency not in ('MOP','HKD'):
            result['held'].append({'code':code,'reason':'Explicit MOP or HKD billing currency required'});continue
        name=item.get('title',code)
        if name.startswith('GRACE PERIOD - '):name=name[len('GRACE PERIOD - '):]
        address='\n'.join(str(customer.get(k,'')).strip() for k in ('Street Address','City','State','Zip','Country') if str(customer.get(k,'')).strip())
        store.save_merchant({'code':code,'name':name,'billing_name':str(customer.get('Company name') or customer['Name']),'email':str(customer.get('Email','')).strip(),'address':address,'currency':currency,'plan':item.get('plan',''),'service_end':end,'access_status':state['status'],'notes':'Imported for review; original Pipedrive due '+str(item.get('nextRenewal'))+'. Identity, billing and service history require operator acceptance.'},'Read-only source import; no historic orders replayed')
        result['imported']+=1
    return result
