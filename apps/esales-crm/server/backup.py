import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path


def backup(store,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('Backup destination already exists')
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);db=root/'crm.sqlite'
        with store.connect() as src,sqlite3.connect(db) as dst:src.backup(dst)
        c=sqlite3.connect(db);c.row_factory=sqlite3.Row
        paths=[dict(row) for row in c.execute('SELECT id,pdf_path FROM receipts WHERE pdf_path IS NOT NULL')];c.close()
        for receipt in paths:
            if not Path(receipt['pdf_path']).is_file():raise ValueError('A referenced PDF is missing; backup refused')
            target=root/'receipts'/(receipt['id']+'.pdf');target.parent.mkdir(exist_ok=True);shutil.copyfile(receipt['pdf_path'],target)
        files=[f for f in root.rglob('*') if f.is_file()]
        manifest={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
        with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for f in files:z.write(f,str(f.relative_to(root)))
            z.writestr('manifest.json',json.dumps(manifest))
    destination.chmod(0o600)
    return {'files':len(manifest),'path':str(destination)}


def restore(archive,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('Restore requires a new destination; existing data is never overwritten')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(set(names))!=len(names) or any(name.startswith('/') or '..' in Path(name).parts or '\\' in name for name in names):raise ValueError('Unsafe archive path')
        if 'manifest.json' not in names or 'crm.sqlite' not in names:raise ValueError('Missing backup manifest or database')
        if sum(i.file_size for i in z.infolist())>500_000_000:raise ValueError('Archive exceeds supported restore size')
        manifest=json.loads(z.read('manifest.json'))
        if set(names)!=set(manifest)|{'manifest.json'}:raise ValueError('Unexpected backup members')
        for name,digest in manifest.items():
            if name!='crm.sqlite' and not (name.startswith('receipts/') and Path(name).name.endswith('.pdf') and len(Path(name).parts)==2):raise ValueError('Unexpected backup file')
            if hashlib.sha256(z.read(name)).hexdigest()!=digest:raise ValueError('Backup checksum mismatch')
        destination.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
            root=Path(tmp)/'restore';root.mkdir(mode=0o700)
            for name in manifest:
                out=root/name;out.parent.mkdir(exist_ok=True,mode=0o700);out.write_bytes(z.read(name));out.chmod(0o600)
            with sqlite3.connect(root/'crm.sqlite') as c:
                if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
                for rid,old in c.execute('SELECT id,pdf_path FROM receipts WHERE pdf_path IS NOT NULL').fetchall():
                    if not (root/'receipts'/(rid+'.pdf')).is_file():raise ValueError('Receipt document missing')
                    c.execute('UPDATE receipts SET pdf_path=? WHERE id=?',(str(destination/'receipts'/(rid+'.pdf')),rid))
                # Restored sessions are invalid; no old browser session is revived.
                c.execute('DELETE FROM sessions')
            os.rename(root,destination)
    return {'path':str(destination)}
