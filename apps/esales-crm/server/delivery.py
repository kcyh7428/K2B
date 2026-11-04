import json
import os
import smtplib
import ssl
import fcntl
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from server.db import now
from server.documents import ensure_pdf
from server.orders import email_ok, hold, text


class DeliveryActive(ValueError):
    pass


@contextmanager
def delivery_lock(store):
    descriptor=os.open(store.path.parent/'.delivery.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise DeliveryActive('A delivery worker is active; stop it before recovery') from None
        yield
    finally:os.close(descriptor)


def recover_deliveries(store):
    with delivery_lock(store),store.transaction() as c:
        return _recover_interrupted(store,c)


def _recover_interrupted(store,c):
    # The caller holds the host delivery lock, so no processing row can be live.
    rows=c.execute("SELECT d.receipt_id,r.order_id FROM deliveries d JOIN receipts r ON r.id=d.receipt_id WHERE d.state='processing'").fetchall()
    error='Worker interrupted; reconcile SMTP outcome before retry'
    c.execute("UPDATE deliveries SET state='uncertain',error=? WHERE state='processing'",(error,))
    for row in rows:hold(c,row['order_id'],'delivery',error)
    if rows:store.audit(c,'delivery.recovery','worker',f'{len(rows)} interrupted jobs held for reconciliation')
    return len(rows)


def process_delivery(store,settings):
    try:
        with delivery_lock(store):return _process_delivery(store,settings)
    except DeliveryActive:return {'state':'busy'}


def _process_delivery(store,settings):
    with store.transaction() as c:
        _recover_interrupted(store,c)
        job=c.execute("SELECT d.*,r.snapshot,r.pdf_path,r.state receipt_state FROM deliveries d JOIN receipts r ON r.id=d.receipt_id WHERE d.state='queued' AND r.state!='void' ORDER BY r.created_at LIMIT 1").fetchone()
        if not job:return {'state':'idle'}
        message_id=f"<{job['receipt_id']}.{job['attempts']+1}@esales-receipts.local>"
        c.execute("UPDATE deliveries SET state='processing',claimed_at=?,attempts=attempts+1,message_id=? WHERE id=?",(now(),message_id,job['id']))
        store.audit(c,'delivery.attempt_started',job['receipt_id'],json.dumps({'attempt':job['attempts']+1,'message_id':message_id}))
    snapshot=json.loads(job['snapshot'])
    path=Path(job['pdf_path']) if job['pdf_path'] else settings.data_dir/'receipts'/(job['receipt_id']+'.pdf')
    state='failed';error='PDF generation failed'
    try:
        path=ensure_pdf(store,job['receipt_id'],settings.data_dir)
        if not settings.delivery_enabled or not settings.smtp_host or not email_ok(settings.smtp_from):
            state='blocked';error='Email sending is disabled or sender/SMTP is not configured'
        elif not settings.smtp_tls and settings.smtp_host not in ('localhost','127.0.0.1','::1'):
            state='blocked';error='TLS is required for remote SMTP delivery'
        elif settings.demo and settings.smtp_host not in ('localhost','127.0.0.1','::1'):
            state='blocked';error='Demo mode does not send email outside a local test listener'
        else:
            error='Could not prepare the receipt email; no message was submitted'
            msg=EmailMessage();msg['Subject']='Payment receipt '+snapshot['number'];msg['From']=settings.smtp_from;msg['To']=job['recipient'] or snapshot['billing']['email'];msg['Message-ID']=message_id
            msg.set_content(f"Thank you for your payment. Your receipt {snapshot['number']} is attached.\nOrder: {snapshot['reference']}\n{snapshot['issuer']['name']}")
            msg.add_attachment(path.read_bytes(),maintype='application',subtype='pdf',filename=snapshot['number']+'.pdf')
            state='failed';error='Email connection or authentication failed; no message was submitted'
            with smtplib.SMTP(settings.smtp_host,settings.smtp_port,timeout=settings.smtp_timeout_seconds) as client:
                if settings.smtp_tls:client.starttls(context=ssl.create_default_context())
                if settings.smtp_user:client.login(settings.smtp_user,settings.smtp_password)
                # Only message submission can have an ambiguous acceptance outcome.
                state='uncertain';error='SMTP outcome uncertain; reconcile before retrying'
                refused=client.send_message(msg)
                if refused:raise smtplib.SMTPRecipientsRefused(refused)
                # DATA was accepted. A later QUIT failure does not undo submission.
                state='submitted';error=None
    except (smtplib.SMTPResponseException,smtplib.SMTPRecipientsRefused,smtplib.SMTPNotSupportedError):
        if state!='submitted':
            state='failed';error='Email service rejected this attempt; no message was accepted'
    except Exception as exc:
        # Do not leak SMTP credentials, provider traces or customer addresses into errors.
        if error:error+=' ('+type(exc).__name__+')'
    with store.transaction() as c:
        c.execute('UPDATE deliveries SET state=?,error=?,submitted_at=? WHERE id=?',(state,error,now() if state=='submitted' else None,job['id']))
        if error:hold(c,store.one('SELECT order_id FROM receipts WHERE id=?',(job['receipt_id'],))['order_id'],'delivery',error)
        else:c.execute("UPDATE exceptions SET resolved_at=?,resolution='Submitted to email service' WHERE order_id=(SELECT order_id FROM receipts WHERE id=?) AND kind='delivery' AND resolved_at IS NULL",(now(),job['receipt_id']))
        store.audit(c,'delivery.'+state,job['receipt_id'],json.dumps({'attempt':job['attempts']+1,'message_id':message_id,'recipient':job['recipient'] or snapshot['billing']['email'],'outcome':error or 'Accepted by SMTP; recipient delivery not yet confirmed'}))
    return {'state':state,'receipt_id':job['receipt_id']}


def retry_delivery(store,receipt_id,reason,recipient=None,allow_duplicate_copy=False):
    text(reason,'resend reason',1000)
    if recipient is not None and not email_ok(recipient):raise ValueError('Invalid resend recipient')
    with store.transaction() as c:
        row=c.execute('SELECT d.*,r.state receipt_state FROM deliveries d JOIN receipts r ON r.id=d.receipt_id WHERE receipt_id=?',(receipt_id,)).fetchone()
        if not row or row['receipt_state']=='void':raise ValueError('Receipt unavailable or void')
        if row['state'] in ('processing','queued'):raise ValueError('Delivery already queued or in progress')
        if row['state'] in ('uncertain','submitted') and (not reason.startswith('RECONCILED:') or not reason[len('RECONCILED:'):].strip()):raise ValueError('Confirm the SMTP outcome first; prefix the reason with RECONCILED: and record the evidence')
        if row['state'] in ('uncertain','submitted','delivered'):
            if allow_duplicate_copy is not True:raise ValueError('Explicitly acknowledge that an earlier copy may have arrived before sending another copy')
            store.audit(c,'delivery.copy_authorized',receipt_id,json.dumps({'previous_state':row['state'],'reason':reason}))
        c.execute("UPDATE deliveries SET state='queued',error=NULL,claimed_at=NULL WHERE id=?",(row['id'],))
        if recipient is not None:c.execute('UPDATE deliveries SET recipient=? WHERE id=?',(recipient,row['id']))
        store.audit(c,'delivery.retry',receipt_id,reason)
        if recipient is not None:store.audit(c,'delivery.recipient_corrected',receipt_id,json.dumps({'recipient':recipient,'reason':reason}))
