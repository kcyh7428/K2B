import json
import zipfile
from pathlib import Path
from server.migration import import_audit
from server.backup import backup,restore
from server.orders import ingest
from server.documents import render_receipt
from server.db import Store


def test_import_is_idempotent_uses_esales_end_and_never_creates_receipts(tmp_path):
    source=tmp_path/'audit';source.mkdir()
    (source/'migration-inventory.json').write_text(json.dumps({'merchantChecks':[{'merchantCode':'SAMPLE','title':'GRACE PERIOD - Sample','plan':'Legacy','nextRenewal':'2026-10-21','quickbooksExactNameMatchCount':1}]}))
    (source/'esales-active-grace-snapshot.json').write_text(json.dumps({'merchants':[{'code':'SAMPLE','end':'2027-10-22','status':'active'}]}))
    # Minimal readable BIFF fixture is not needed: the import consumes a normalized private billing map.
    (source/'billing-map.json').write_text(json.dumps([{'Name':'SAMPLE','Company name':'Sample Company','Email':'merchant@example.test','Street Address':'Macau','Currency':'MOP'}]))
    store=Store(tmp_path/'db.sqlite')
    assert import_audit(store,source)['imported']==1
    assert import_audit(store,source)['skipped']==1
    assert store.one('select service_end from merchants')['service_end']=='2027-10-22'
    assert store.one('select name from merchants')['name']=='Sample'
    assert store.one('select count(*) n from receipts')['n']==0


def test_backup_restores_database_and_pdf_without_replay(store,event,tmp_path):
    ingest(store,event)
    row=store.one('select * from receipts');path=tmp_path/'receipts'/(row['id']+'.pdf')
    render_receipt(json.loads(row['snapshot']),path)
    with store.transaction() as c:c.execute('update receipts set pdf_path=? where id=?',(str(path),row['id']))
    archive=tmp_path/'backup.zip';backup(store,archive)
    restored=tmp_path/'restored';restore(archive,restored)
    result=Store(restored/'crm.sqlite')
    assert result.one('select count(*) n from orders')['n']==1
    assert Path(result.one('select pdf_path from receipts')['pdf_path']).read_bytes()==path.read_bytes()


def test_restore_refuses_path_traversal_and_existing_destination(tmp_path):
    import pytest
    f=tmp_path/'bad.zip'
    with zipfile.ZipFile(f,'w') as z:z.writestr('../escape','bad')
    with pytest.raises(ValueError):restore(f,tmp_path/'new')
    assert not (tmp_path/'escape').exists()
    with pytest.raises(ValueError):restore(f,tmp_path)
