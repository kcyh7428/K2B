import hashlib
import json
import re
import secrets
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from server.config import Settings, verify_password
from server.db import Store, now, VersionConflict
from server.orders import ingest, release, email_ok, text
from server.delivery import process_delivery, retry_delivery
from server.documents import ensure_pdf
from server.automation import get_policy,save_policy,plan_actions,claim_action,finish_action,requeue_action


class Login(BaseModel):
    password: str = Field(max_length=200)


class Merchant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1,max_length=120,pattern=r'^[\w.+-]+$')
    name: str = Field(min_length=1,max_length=200)
    billing_name: str = Field(default='',max_length=300)
    email: str = Field(default='',max_length=254)
    address: str = Field(default='',max_length=3000)
    currency: str = Field(default='MOP',pattern=r'^(MOP|HKD)$')
    plan: str = Field(default='',max_length=200)
    service_end: date | None = None
    access_status: str = Field(default='unknown',pattern=r'^(unknown|active|grace|inactive)$')
    notes: str = Field(default='',max_length=5000)
    phone: str = Field(default='',max_length=30)
    reason: str = Field(default='New merchant record',min_length=3,max_length=1000)
    expected_updated_at: str | None = None


class Reason(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=3,max_length=1000)


class Resend(Reason):
    recipient: str | None = Field(default=None,max_length=254)
    allow_duplicate_copy: bool = Field(default=False,strict=True)


def create_app(settings=None):
    settings=settings or Settings.from_env()
    if settings.intake_environment not in ('production','staging'):
        raise RuntimeError('CRM_INTAKE_ENVIRONMENT must be production or staging')
    if not settings.demo and (not settings.password_hash or len(settings.intake_secret)<32):
        raise RuntimeError('Configure operator password hash and an intake secret of at least 32 characters')
    if not settings.demo and urlsplit(settings.public_url).hostname not in ('localhost','127.0.0.1','testserver') and not settings.public_url.startswith('https://'):
        raise RuntimeError('A public installation requires HTTPS')
    store=Store(settings.data_dir/'crm.sqlite')
    store.bind_mode('demo' if settings.demo else 'private')
    if not store.one('SELECT id FROM businesses WHERE id=?',('converto',)):
        store.save_business('converto',{'name':'Converto Digital','address':'','email':''},'CD-NEW-',False)
    app=FastAPI(title='e-sales CRM',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store

    @app.middleware('http')
    async def guard(request,call_next):
        host=urlsplit(settings.public_url).hostname
        if request.url.hostname not in (host,'localhost' if host=='127.0.0.1' else host):return JSONResponse({'detail':'Unrecognized host'},400)
        if settings.demo and request.client and request.client.host not in ('127.0.0.1','::1','testclient'):return JSONResponse({'detail':'Demo is local only'},403)
        if request.method in ('POST','PUT','DELETE','PATCH'):
            size=0;parts=[]
            async for part in request.stream():
                size+=len(part)
                if size>1_000_000:return JSONResponse({'detail':'Payload too large'},413)
                parts.append(part)
            request._body=b''.join(parts)
            if not request.url.path.startswith('/hooks/') and request.headers.get('origin') != settings.public_url.rstrip('/'):
                return JSONResponse({'detail':'Same-origin request required'},403)
        if request.url.path.startswith('/api/') and request.url.path not in ('/api/login',):
            token=request.cookies.get('crm_session','')
            valid=store.one('SELECT expires_at FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)) if token else None
            if not settings.demo and (not valid or valid['expires_at']<int(time.time())):return JSONResponse({'detail':'Sign in required'},401)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)},409 if isinstance(exc,VersionConflict) else 422)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        errors=[{key:error[key] for key in ('loc','msg','type') if key in error} for error in exc.errors()]
        return JSONResponse({'detail':errors},422)

    @app.get('/health')
    def health():return {'status':'ok'}

    @app.get('/api/readiness')
    def readiness():
        from server.documents import FONT
        company=store.one("SELECT issue_enabled FROM businesses WHERE id='converto'")
        return {'checks':{'database':True,'receipt_font':FONT.is_file(),'web_build':(Path(__file__).resolve().parent.parent/'web/dist/index.html').exists(),'issuer_approved':bool(company['issue_enabled']),'smtp_configured':bool(settings.smtp_host and settings.smtp_from),'delivery_enabled':settings.delivery_enabled,'automation_enabled':get_policy(store)['enabled']},'legacy_receipts':store.one("SELECT count(*) n FROM orders WHERE state='legacy'")['n'],'cutover_accepted':False,'note':'Live source reconciliation, sender test and access-policy acceptance remain separate cutover gates.'}

    @app.post('/api/login')
    def login(body:Login,request:Request):
        key=request.client.host if request.client else 'unknown';ts=int(time.time())
        with store.transaction() as c:
            row=c.execute('SELECT * FROM rate_limits WHERE key=?',(key,)).fetchone()
            if row and row['until_at']>ts and row['count']>=8:
                store.audit(c,'auth.login_limited',key,'Login refused by temporary rate limit; no submitted credentials retained')
                return JSONResponse({'detail':'Too many attempts; wait 15 minutes'},429)
            count=row['count']+1 if row and row['until_at']>ts else 1
            c.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?)',(key,count,row['until_at'] if row and row['until_at']>ts else ts+900))
        if not verify_password(body.password,settings.password_hash):
            with store.transaction() as c:store.audit(c,'auth.login_failed',key,'Invalid password; no submitted credentials retained')
            raise HTTPException(401,'Invalid credentials')
        token=secrets.token_urlsafe(40)
        with store.transaction() as c:
            c.execute('DELETE FROM sessions WHERE expires_at<?',(ts,))
            c.execute('DELETE FROM rate_limits WHERE key=?',(key,))
            c.execute('INSERT INTO sessions VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),ts+43200))
            store.audit(c,'auth.login_succeeded',key,'Operator session created; failed-attempt counter cleared')
        response=JSONResponse({'ok':True});response.set_cookie('crm_session',token,max_age=43200,httponly=True,secure=settings.public_url.startswith('https://'),samesite='strict')
        return response

    @app.post('/api/logout')
    def logout(request:Request):
        with store.transaction() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get('crm_session','').encode()).hexdigest(),))
        response=JSONResponse({'ok':True});response.delete_cookie('crm_session');return response

    @app.get('/api/session')
    def session():return {'user':'Keith' if not settings.demo else 'Demo operator','demo':settings.demo}

    def merchants():
        today=datetime.now(ZoneInfo('Asia/Macau')).date()
        rows=store.all('SELECT * FROM merchants ORDER BY name COLLATE NOCASE')
        for row in rows:
            try:delta=(date.fromisoformat(row['service_end'])-today).days if row['service_end'] else None
            except (ValueError,TypeError):delta=None
            row['days_until_end']=delta
            row['renewal_state']='needs_review' if delta is None else 'overdue' if delta<0 else 'due_soon' if delta<=30 else 'current'
        return rows

    @app.get('/api/merchants')
    def list_merchants():return merchants()

    @app.post('/api/merchants')
    def create_merchant(body:Merchant):
        if store.one('SELECT id FROM merchants WHERE code=?',(body.code,)):raise HTTPException(409,'Merchant code already exists')
        return save_merchant(body,create_only=True)

    def save_merchant(body,create_only=False):
        if body.email and not email_ok(body.email):raise ValueError('Invalid billing email')
        data=body.model_dump(mode='json',exclude={'reason','expected_updated_at'})
        return {'id':store.save_merchant(data,body.reason,expected_updated_at=body.expected_updated_at,create_only=create_only)}

    @app.put('/api/merchants/{merchant_id}')
    def update_merchant(merchant_id:str,body:Merchant):
        old=store.one('SELECT * FROM merchants WHERE id=?',(merchant_id,))
        if not old:raise HTTPException(404,'Merchant not found')
        if body.code.casefold()!=old['code'].casefold():raise ValueError('Merchant code is immutable')
        if body.expected_updated_at!=old['updated_at']:raise HTTPException(409,'Record changed; reload before editing')
        return save_merchant(body)

    @app.get('/api/orders')
    def orders():return store.all("SELECT o.*,r.id receipt_id,r.number receipt_number,r.state receipt_state,d.state delivery_state,d.attempts delivery_attempts,d.error delivery_error,COALESCE(d.recipient,json_extract(r.snapshot,'$.billing.email')) delivery_recipient FROM orders o LEFT JOIN receipts r ON r.order_id=o.id LEFT JOIN deliveries d ON d.receipt_id=r.id ORDER BY o.created_at DESC")

    @app.post('/api/orders')
    def manual_order(body:dict):
        if body.get('payment_confirmed') is not True:raise ValueError('Confirm the reviewed payment proof first')
        reason=text(body.get('reason'),'approval reason',1000)
        payload=body.get('payload')
        if not isinstance(payload,dict):raise ValueError('Expected payment payload')
        # Manual entries belong to this server. Webhook environment checks stay strict.
        payload={**payload,'env':settings.intake_environment}
        return ingest(store,payload,environment=settings.intake_environment,manual_reason=reason)

    def hook_auth(request,key):
        supplied=request.headers.get('authorization','')
        if not key or not secrets.compare_digest(supplied.encode(),('Bearer '+key).encode()):raise HTTPException(401,'Invalid intake credentials')

    @app.get('/hooks/merchants/{code}/renewal')
    def renewal_context(code:str,request:Request):
        hook_auth(request,settings.intake_secret)
        merchant=store.one('SELECT code,name,service_end,access_status,phone FROM merchants WHERE code=?',(code,))
        if not merchant:raise HTTPException(404,'Merchant not found')
        today=datetime.now(ZoneInfo('Asia/Macau')).date()
        try:days_until_end=(date.fromisoformat(merchant['service_end'])-today).days
        except (ValueError,TypeError):days_until_end=None
        with store.transaction() as c:
            store.audit(c,'hook.renewal_read',merchant['code'],'Read current renewal context for Make')
        renewal_state='needs_review' if days_until_end is None else 'overdue' if days_until_end<0 else 'due_soon' if days_until_end<=30 else 'current'
        return {**merchant,'as_of':today.isoformat(),'days_until_end':days_until_end,'renewal_state':renewal_state}

    @app.post('/hooks/esales')
    def hook(body:dict,request:Request):
        hook_auth(request,settings.intake_secret)
        return ingest(store,body,environment=settings.intake_environment)

    @app.post('/hooks/email')
    def email_event(body:dict,request:Request):
        hook_auth(request,settings.email_hook_secret)
        message_id=text(body.get('message_id'),'message id',254)
        if body.get('status') not in ('delivered','bounced'):raise ValueError('Unknown delivery event')
        with store.transaction() as c:
            row=c.execute('SELECT d.*,r.order_id,r.state receipt_state FROM deliveries d JOIN receipts r ON r.id=d.receipt_id WHERE d.message_id=?',(message_id,)).fetchone()
            if not row:
                match=re.fullmatch(r'<([0-9a-f]{32})\.([0-9]+)@esales-receipts\.local>',str(body.get('message_id','')))
                old=c.execute('SELECT * FROM deliveries WHERE receipt_id=?',(match[1],)).fetchone() if match else None
                if old and 0<int(match[2])<old['attempts']:
                    issued=c.execute("SELECT 1 FROM events WHERE subject=? AND action IN ('delivery.attempt_started','delivery.submitted','delivery.uncertain','delivery.failed','delivery.blocked') AND CASE WHEN json_valid(detail) THEN json_extract(detail,'$.message_id') END=? LIMIT 1",(match[1],body['message_id'])).fetchone()
                    if issued:
                        store.audit(c,'delivery.historical_callback',match[1],json.dumps({'message_id':body['message_id'],'status':body['status']}))
                        return {'ok':True,'historical':True}
                raise HTTPException(404,'Message not found')
            if row['receipt_state']=='void':
                store.audit(c,'delivery.void_callback',row['receipt_id'],json.dumps({'message_id':body['message_id'],'status':body['status']}))
                return {'ok':True,'void':True}
            if row['state'] not in ('submitted','uncertain','delivered','bounced'):raise ValueError('Message submission is not yet ready for a final provider outcome')
            if row['state'] in ('delivered','bounced'):
                if row['state']==body['status']:return {'ok':True,'duplicate':True}
                from server.orders import hold
                hold(c,row['order_id'],'delivery','Conflicting email outcomes; reconcile provider evidence before any retry')
                store.audit(c,'delivery.callback_conflict',row['receipt_id'],json.dumps({'recorded':row['state'],'received':body['status']}))
                return {'ok':True,'conflict':True}
            c.execute('UPDATE deliveries SET state=?,error=NULL WHERE id=?',(body['status'],row['id']))
            if body['status']=='bounced':
                from server.orders import hold
                hold(c,row['order_id'],'delivery','Email bounced; verify billing recipient before resending')
            else:
                c.execute("UPDATE exceptions SET resolved_at=?,resolution='Provider confirmed delivery' WHERE order_id=? AND kind='delivery' AND resolved_at IS NULL",(now(),row['order_id']))
            store.audit(c,'delivery.'+body['status'],row['receipt_id'],'Authenticated email event')
        return {'ok':True}

    @app.post('/hooks/actions/plan')
    def plan_hook(request:Request):
        hook_auth(request,settings.intake_secret)
        return plan_actions(store)

    @app.post('/hooks/actions/{key}/claim')
    def claim_hook(key:str,request:Request):
        hook_auth(request,settings.intake_secret)
        return claim_action(store,key)

    @app.post('/hooks/actions/{key}/finish')
    def finish_hook(key:str,body:dict,request:Request):
        hook_auth(request,settings.intake_secret)
        return finish_action(store,key,body.get('status'),body.get('observed_status'))

    @app.get('/api/policy')
    def read_policy():return get_policy(store)

    @app.put('/api/policy')
    def write_policy(body:dict):save_policy(store,body);return get_policy(store)

    @app.get('/api/actions')
    def read_actions():return store.all('SELECT * FROM actions ORDER BY created_at DESC LIMIT 500')

    @app.post('/api/actions/{key}/retry')
    def retry_action(key:str,body:Reason):return requeue_action(store,key,body.reason)

    @app.post('/api/orders/{order_id}/release')
    def release_order(order_id:str,body:Reason):return {'receipt_id':release(store,order_id,body.reason)}

    @app.get('/api/receipts/{receipt_id}/pdf')
    def pdf(receipt_id:str):
        row=store.one('SELECT * FROM receipts WHERE id=?',(receipt_id,))
        if not row:raise HTTPException(404,'Receipt not found')
        path=ensure_pdf(store,receipt_id,settings.data_dir)
        return FileResponse(path,media_type='application/pdf',filename=row['number']+'.pdf',content_disposition_type='inline')

    @app.post('/api/receipts/{receipt_id}/retry')
    def retry(receipt_id:str,body:Resend):retry_delivery(store,receipt_id,body.reason,recipient=body.recipient,allow_duplicate_copy=body.allow_duplicate_copy);return {'ok':True}

    @app.post('/api/receipts/{receipt_id}/void')
    def void(receipt_id:str,body:Reason):
        with store.transaction() as c:
            row=c.execute('SELECT r.id,r.order_id,r.state receipt_state,o.kind,o.merchant_code,d.state FROM receipts r JOIN orders o ON o.id=r.order_id LEFT JOIN deliveries d ON d.receipt_id=r.id WHERE r.id=?',(receipt_id,)).fetchone()
            if not row:raise HTTPException(404,'Receipt not found')
            if row['receipt_state']=='void':raise HTTPException(409,'Receipt is already void')
            if row['receipt_state']=='legacy':raise HTTPException(409,'Historical receipts are outside this CRM correction workflow')
            if row['state'] is None:raise HTTPException(409,'Receipt delivery record is missing; reconcile stored data first')
            if row['state'] in ('processing','uncertain','submitted'):raise HTTPException(409,'Reconcile the external email outcome before voiding this receipt')
            c.execute("UPDATE receipts SET state='void' WHERE id=?",(receipt_id,))
            c.execute("UPDATE deliveries SET state='cancelled' WHERE receipt_id=? AND state IN ('queued','blocked','failed')",(receipt_id,))
            store.audit(c,'receipt.void',receipt_id,body.reason)
            if row['kind']=='renewal':
                from server.orders import hold
                hold(c,row['order_id'],'service_reconciliation','Renewal receipt voided for '+row['merchant_code']+'; verify payment and actual e-sales service end, then correct the CRM date if needed. Document voiding does not reverse the observed service term.')
            if row['state']=='delivered':
                from server.orders import hold
                hold(c,row['order_id'],'delivery','Previously delivered receipt voided; reconcile any correction with its recipient')
        return {'ok':True,'service_reconciliation_required':row['kind']=='renewal','delivery_reconciliation_required':row['state']=='delivered'}

    @app.post('/api/deliveries/process')
    def process():return process_delivery(store,settings)

    @app.get('/api/exceptions')
    def exceptions():return store.all('SELECT e.*,o.reference,o.merchant_code FROM exceptions e LEFT JOIN orders o ON o.id=e.order_id ORDER BY e.created_at DESC')

    @app.post('/api/exceptions/{exception_id}/resolve')
    def resolve_exception(exception_id:str,body:Reason):
        with store.transaction() as c:
            row=c.execute('SELECT e.*,o.state order_state FROM exceptions e LEFT JOIN orders o ON o.id=e.order_id WHERE e.id=?',(exception_id,)).fetchone()
            if not row:raise HTTPException(404,'Review issue not found')
            if row['resolved_at'] is not None:raise HTTPException(409,'Review issue is already resolved')
            if row['order_state']=='review' and row['kind']!='conflict':raise ValueError('Release or correct the held payment first; it cannot be dismissed')
            c.execute('UPDATE exceptions SET resolved_at=?,resolution=? WHERE id=?',(now(),body.reason,exception_id))
            store.audit(c,'exception.resolved',exception_id,body.reason)
        return {'ok':True}

    @app.get('/api/events')
    def events():return store.all('SELECT * FROM events ORDER BY created_at DESC LIMIT 500')

    @app.get('/api/overview')
    def overview():
        rows=merchants()
        return {'merchants':len(rows),'due_soon':sum(r['renewal_state']=='due_soon' for r in rows),'overdue':sum(r['renewal_state']=='overdue' for r in rows),'unresolved':store.one('SELECT count(*) n FROM exceptions WHERE resolved_at IS NULL')['n'],'queued':store.one("SELECT count(*) n FROM deliveries WHERE state='queued'")['n'],'orders':store.one('SELECT count(*) n FROM orders')['n'],'last_event':store.one("SELECT created_at FROM events WHERE action='order.received' ORDER BY created_at DESC LIMIT 1"),'reminders_enabled':get_policy(store)['enabled'],'access_changes_enabled':get_policy(store)['enabled'],'delivery_enabled':settings.delivery_enabled,'demo':settings.demo}

    @app.get('/api/settings')
    def get_settings():
        row=store.one('SELECT * FROM businesses WHERE id=?',('converto',))
        return {'issuer':json.loads(row['issuer']),'prefix':row['prefix'],'issue_enabled':bool(row['issue_enabled']),'sequence':row['sequence'],'smtp_configured':bool(settings.smtp_host and settings.smtp_from),'delivery_enabled':settings.delivery_enabled,'reminder_basis':'e-sales service end','automated_access_changes':False}

    @app.put('/api/settings')
    def put_settings(body:dict):
        issuer=body.get('issuer',{})
        if not isinstance(issuer,dict):raise ValueError('Complete issuer name, address and email required')
        for key in ('name','address','email'):text(issuer.get(key),'issuer '+key,2000)
        if not email_ok(issuer['email']):raise ValueError('Invalid issuer email')
        prefix=body.get('prefix','')
        if not re.fullmatch(r'[A-Z0-9-]{2,30}',prefix):raise ValueError('Use an explicit receipt prefix of 2 to 30 uppercase letters, digits or hyphens')
        if not isinstance(body.get('issue_enabled'),bool):raise ValueError('Explicit issuer approval required')
        store.save_business('converto',{k:issuer[k] for k in ('name','address','email')},prefix,body['issue_enabled'])
        return get_settings()

    @app.get('/api/export')
    def export():return {table:store.all('SELECT * FROM '+table) for table in ('businesses','merchants','orders','receipts','deliveries','exceptions','events','actions')}

    dist=Path(__file__).resolve().parent.parent/'web/dist'
    if dist.exists():app.mount('/',StaticFiles(directory=dist,html=True),name='web')
    return app
