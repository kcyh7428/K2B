import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


class VersionConflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS businesses(id TEXT PRIMARY KEY, issuer TEXT NOT NULL, prefix TEXT NOT NULL, issue_enabled INTEGER NOT NULL DEFAULT 0, sequence INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS merchants(id TEXT PRIMARY KEY, code TEXT NOT NULL COLLATE NOCASE UNIQUE, name TEXT NOT NULL, billing_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'MOP', plan TEXT NOT NULL DEFAULT '', service_end TEXT, access_status TEXT NOT NULL DEFAULT 'unknown', notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, business TEXT NOT NULL REFERENCES businesses(id), environment TEXT NOT NULL, reference TEXT NOT NULL, merchant_code TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, total_minor INTEGER NOT NULL, currency TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(business,environment,reference));
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id), number TEXT NOT NULL UNIQUE, snapshot TEXT NOT NULL, pdf_path TEXT, state TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL REFERENCES receipts(id), state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT NOT NULL, error TEXT, claimed_at TEXT, submitted_at TEXT);
            CREATE TABLE IF NOT EXISTS exceptions(id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(id), kind TEXT NOT NULL, detail TEXT NOT NULL, resolved_at TEXT, resolution TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, action TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY, count INTEGER NOT NULL, until_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,merchant_code TEXT NOT NULL,service_end TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,finished_at TEXT);
            ''')
            if 'phone' not in [r['name'] for r in c.execute('PRAGMA table_info(merchants)')]:
                c.execute("ALTER TABLE merchants ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
                c.commit()
            if 'recipient' not in [r['name'] for r in c.execute('PRAGMA table_info(deliveries)')]:
                c.execute('ALTER TABLE deliveries ADD COLUMN recipient TEXT')
                c.commit()
            if 'claimed_at' not in [r['name'] for r in c.execute('PRAGMA table_info(actions)')]:
                c.execute('ALTER TABLE actions ADD COLUMN claimed_at TEXT')
                c.commit()
        os.chmod(self.path, 0o600)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA synchronous=FULL')
        return c

    @contextmanager
    def transaction(self):
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def all(self, query, params=()):
        with closing(self.connect()) as c:
            return [dict(row) for row in c.execute(query, params).fetchall()]

    def bind_mode(self, mode):
        with self.transaction() as c:
            old=c.execute("SELECT value FROM metadata WHERE key='instance_mode'").fetchone()
            if old and old['value']!=mode:raise ValueError('Database mode differs; demo and private data must remain separate')
            if not old and mode=='demo' and c.execute('SELECT 1 FROM merchants LIMIT 1').fetchone():raise ValueError('Unmarked populated database cannot enter demo mode')
            c.execute("INSERT OR IGNORE INTO metadata VALUES('instance_mode',?)",(mode,))

    def one(self, query, params=()):
        rows = self.all(query, params)
        return rows[0] if rows else None

    @staticmethod
    def audit(c, action, subject, detail):
        c.execute('INSERT INTO events VALUES(?,?,?,?,?)', (uid(), action, subject, detail, now()))

    def save_business(self, key, issuer, prefix, enabled=False):
        with self.transaction() as c:
            existing = c.execute('SELECT prefix,sequence FROM businesses WHERE id=?', (key,)).fetchone()
            if existing and existing['prefix'] != prefix and existing['sequence']>0:
                raise ValueError('Receipt series is locked after first issue')
            c.execute('INSERT INTO businesses(id,issuer,prefix,issue_enabled) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET issuer=excluded.issuer,prefix=excluded.prefix,issue_enabled=excluded.issue_enabled', (key, json.dumps(issuer, ensure_ascii=False), prefix, int(enabled)))
            self.audit(c, 'business.settings', key, 'Issuer and receipt configuration updated')

    def save_merchant(self, data, reason='Created', expected_updated_at=None, create_only=False):
        allowed = ['code','name','billing_name','email','address','currency','plan','service_end','access_status','notes','phone']
        with self.transaction() as c:
            old = c.execute('SELECT * FROM merchants WHERE code=?', (data['code'],)).fetchone()
            if create_only and old:raise VersionConflict('Merchant code already exists')
            if expected_updated_at is not None and (not old or old['updated_at']!=expected_updated_at):
                raise VersionConflict('Record changed; reload before editing')
            values = dict(old) if old else {k: '' for k in allowed}
            values.update({k: data[k] for k in allowed if k in data})
            values['currency'] = values.get('currency') or 'MOP'
            values['access_status'] = values.get('access_status') or 'unknown'
            try:values['service_end']=date.fromisoformat(values['service_end']).isoformat() if values.get('service_end') else None
            except (ValueError,TypeError):raise ValueError('Invalid service end date') from None
            if values['access_status'] not in ('unknown','active','grace','inactive'):raise ValueError('Invalid merchant access status')
            key = old['id'] if old else uid()
            c.execute(f"INSERT INTO merchants(id,{','.join(allowed)},updated_at) VALUES({','.join('?' for _ in range(len(allowed)+2))}) ON CONFLICT(code) DO UPDATE SET {','.join(k+'=excluded.'+k for k in allowed if k!='code')},updated_at=excluded.updated_at", [key]+[values.get(k) for k in allowed]+[now()])
            phone_changed=bool(old and old['phone']!=values['phone'])
            c.execute("UPDATE actions SET state='cancelled' WHERE merchant_code=? COLLATE NOCASE AND state='queued' AND (service_end IS NOT ? OR ?='inactive' OR (kind='reminder' AND ?))",(values['code'],values['service_end'],values['access_status'],phone_changed))
            self.audit(c, 'merchant.updated' if old else 'merchant.created', key, reason)
        return key
