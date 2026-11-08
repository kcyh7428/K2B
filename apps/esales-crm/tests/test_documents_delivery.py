import json
import threading
from email import policy
from email.parser import BytesParser
from aiosmtpd.controller import Controller
from pypdf import PdfReader
from server.config import Settings
from server.documents import render_receipt, FONT, SEMIBOLD_FONT
from server.delivery import process_delivery, retry_delivery
from server.orders import ingest


def test_receipt_assets_have_regular_and_semibold_static_weights():
    # Read the SFNT directory without adding a build-only font dependency.
    import struct
    for path,weight in ((FONT,400),(SEMIBOLD_FONT,600)):
        data=path.read_bytes()
        count=struct.unpack_from('>H',data,4)[0]
        tables={}
        for index in range(count):
            tag,checksum,offset,length=struct.unpack_from('>4sIII',data,12+index*16)
            tables[tag]=(offset,length)
        assert b'fvar' not in tables
        assert struct.unpack_from('>H',data,tables[b'OS/2'][0]+4)[0]==weight


def test_pdf_contains_paid_amount_reference_chinese_and_embedded_font(store, event, tmp_path):
    ingest(store, event)
    snapshot = json.loads(store.one('select snapshot from receipts')['snapshot'])
    out = tmp_path/'test.pdf'
    render_receipt(snapshot, out)
    reader = PdfReader(out)
    content = ''.join(p.extract_text() for p in reader.pages)
    assert 'TEST-2026-00001' in content
    assert '15,400.00' in content and 'sample-order' in content
    assert '系統月費' in content
    assert '/FontFile2' in str(reader.pages[0]['/Resources']['/Font'].get_object().values()) or any('/FontFile2' in str(v.get_object().get('/FontDescriptor',{}).get_object()) for v in reader.pages[0]['/Resources']['/Font'].get_object().values() if v.get_object().get('/FontDescriptor'))


def test_long_receipt_paginates_without_losing_items(store, event, tmp_path):
    ingest(store, event)
    snapshot = json.loads(store.one('select snapshot from receipts')['snapshot'])
    snapshot['lines'] = [{'description': f'第{i}項服務 '+ '易代幣測試 '*30,'quantity':1,'unit_minor':100,'amount_minor':100,'type':'etoken'} for i in range(70)]
    out = tmp_path/'long.pdf';render_receipt(snapshot,out)
    reader=PdfReader(out)
    assert len(reader.pages)>1
    assert '第69項' in ''.join(p.extract_text() for p in reader.pages)


def test_unconfigured_mail_is_failed_visibly_without_claiming_delivery(store,event,tmp_path):
    ingest(store,event)
    settings=Settings(data_dir=tmp_path, demo=True)
    assert process_delivery(store,settings)['state']=='blocked'
    assert store.one('select state from deliveries')['state']=='blocked'
    assert store.one('select count(*) n from receipts')['n']==1


def test_real_smtp_receives_pdf_and_retry_preserves_number(store,event,tmp_path):
    class Sink:
        messages=[]
        async def handle_DATA(self,server,session,envelope):
            self.messages.append(envelope.content)
            return '250 accepted'
    sink=Sink()
    # Bind an available test listener using a free ephemeral port.
    import socket
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    controller=Controller(sink,hostname='127.0.0.1',port=port);controller.start()
    try:
        ingest(store,event)
        settings=Settings(data_dir=tmp_path, smtp_host='127.0.0.1',smtp_port=port,smtp_tls=False,smtp_from='billing@example.test',delivery_enabled=True,demo=True)
        assert process_delivery(store,settings)['state']=='submitted'
        msg=BytesParser(policy=policy.default).parsebytes(sink.messages[0])
        assert msg['To']=='merchant@example.test'
        assert list(msg.iter_attachments())[0].get_payload(decode=True).startswith(b'%PDF')
        receipt=store.one('select * from receipts')
        retry_delivery(store,receipt['id'],'RECONCILED: Corrected recipient verified; additional copy deliberately approved',recipient='corrected@example.test',allow_duplicate_copy=True)
        assert process_delivery(store,settings)['state']=='submitted'
        assert store.one('select count(*) n from receipts')['n']==1
        assert len(sink.messages)==2
        assert BytesParser(policy=policy.default).parsebytes(sink.messages[1])['To']=='corrected@example.test'
        assert BytesParser(policy=policy.default).parsebytes(sink.messages[1])['Message-ID']!=msg['Message-ID']
    finally:controller.stop()
