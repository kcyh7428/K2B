import argparse
import getpass
import json
import time
from datetime import date,timedelta
from pathlib import Path
from server.config import Settings,hash_password
from server.db import Store
from server.orders import ingest
from server.delivery import process_delivery,recover_deliveries
from server.automation import recover_actions
from server.migration import import_audit
from server.backup import backup,restore


def main():
    parser=argparse.ArgumentParser(description='e-sales CRM local operations')
    parser.add_argument('command',choices=['demo','password-hash','import-audit','worker','recover-delivery','recover-actions','backup','restore'])
    parser.add_argument('path',nargs='?');parser.add_argument('--destination');parser.add_argument('--once',action='store_true')
    args=parser.parse_args();settings=Settings.from_env()
    if args.command=='password-hash':
        if not args.path:parser.error('Supply a private output path for the password hash')
        password=getpass.getpass('New operator password (14+ characters): ')
        if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords differ')
        target=Path(args.path)
        with target.open('x') as f:f.write(hash_password(password)+'\n')
        target.chmod(0o600);print('Password hash saved privately. No password printed.');return
    if args.command=='restore':
        if not args.path or not args.destination:parser.error('Supply backup path and --destination NEW_DIRECTORY')
        print(json.dumps(restore(args.path,args.destination)));return
    store=Store(settings.data_dir/'crm.sqlite')
    store.bind_mode('demo' if settings.demo else 'private')
    if args.command=='demo':
        if not settings.demo:raise ValueError('Demo seeding requires CRM_DEMO=1 and a separate demo data directory')
        if store.one('SELECT id FROM merchants'):raise ValueError('Demo seed requires an empty store')
        store.save_business('converto',{'name':'Converto Digital · SAMPLE','address':'Sample business address, Macau','email':'billing@example.test'},'DEMO-2026-',True)
        today=date.today()
        for code,name,offset,plan in [('SAMPLE01','Camellia Studio',18,'Legacy 1200'),('SAMPLE02','Little Orchard',4,'Legacy 880'),('SAMPLE03','Harbour & Co.',-2,'Business 1400'),('SAMPLE04','Paper Moon',85,'Legacy 1200'),('SAMPLE05','Olive Workshop',46,'Legacy 880'),('SAMPLE06','Moss Atelier',-1,'Business 1400')]:
            store.save_merchant({'code':code,'name':name,'billing_name':name,'email':'merchant@example.test','address':'Sample address, Macau','currency':'MOP','plan':plan,'service_end':(today+timedelta(days=offset)).isoformat(),'access_status':'grace' if offset<0 else 'active'})
        for code,kind,reference,qty,rate in [('SAMPLE01','monthly','DEMO-ORDER-001',12,1200),('SAMPLE04','etoken','DEMO-ORDER-002',1,400)]:
            ingest(store,{'env':'production','data':{'code':reference,'merchant_code':code,'currency':'MOP','status':'completed','merchant_subscription_end_date':(today+timedelta(days=383)).isoformat(),'coupon_discount':0,'price':qty*rate,'payment_price':qty*rate,'payment':{'name':'Bank transfer'},'items':[{'system_product_type':kind,'system_product_name':'系統月費 · Sample subscription' if kind=='monthly' else '易代幣 4000枚 · Sample top-up','quantity':qty,'unit_price':rate}]}})
        process_delivery(store,settings);print('Synthetic demo seeded; email sending remains disabled.')
    elif args.command=='import-audit':
        if settings.demo:raise ValueError('Real source imports are forbidden in demo mode')
        if not args.path:parser.error('Supply the private audit folder')
        print(json.dumps(import_audit(store,args.path)))
    elif args.command=='backup':
        if not args.path:parser.error('Supply a new backup file path')
        print(json.dumps(backup(store,args.path)))
    elif args.command=='recover-delivery':
        print(json.dumps({'held':recover_deliveries(store)}))
    elif args.command=='recover-actions':
        print(json.dumps({'held':recover_actions(store)}))
    elif args.command=='worker':
        while True:
            print(json.dumps(process_delivery(store,settings)),flush=True)
            if args.once:break
            time.sleep(5)


if __name__=='__main__':main()
