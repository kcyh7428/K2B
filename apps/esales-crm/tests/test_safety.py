import pytest
from server.db import Store
from server.api import create_app
from server.config import Settings
from server.orders import ingest


def test_demo_cannot_open_a_private_import_database(tmp_path):
    store=Store(tmp_path/'crm.sqlite');store.bind_mode('private')
    with pytest.raises(ValueError,match='mode'):create_app(Settings(data_dir=tmp_path,demo=True))


def test_atomic_pdf_preview_and_worker_reuse_same_document(store,event,tmp_path):
    from server.documents import ensure_pdf
    from concurrent.futures import ThreadPoolExecutor
    ingest(store,event);rid=store.one('select id from receipts')['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths=list(pool.map(lambda _:ensure_pdf(store,rid,tmp_path),range(4)))
    assert len(set(str(p) for p in paths))==1
    assert paths[0].read_bytes().startswith(b'%PDF')
