#!/usr/bin/env python3
"""Read-only Changqian SQLite -> canonical ledger, preserving broker/source IDs."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import quote
import ledger

def migrate(root,path):
    src=sqlite3.connect('file:'+quote(str(Path(path).resolve()))+'?mode=ro',uri=True)
    src.row_factory=sqlite3.Row
    if src.execute('pragma integrity_check').fetchone()[0]!='ok':raise ValueError('Source SQLite integrity check failed')
    with tempfile.TemporaryDirectory() as td:
        snapshot=Path(td)/'changqian-snapshot.db'
        dest=sqlite3.connect(snapshot);src.backup(dest);dest.close()
        source=ledger.archive_source(root,snapshot)
    sid=source['id'];batch={k:[] for k in ledger.COLLECTIONS};batch['sources']=[source]
    tables={r[0] for r in src.execute("select name from sqlite_master where type='table'")}
    accounts=[dict(r) for r in src.execute('select * from assets order by sort_order')]
    records=[dict(r) for r in src.execute('select * from records')]
    # Match source app: auto valuation last on a date; retain actual creation ordering otherwise.
    records.sort(key=lambda r:(r['business_date'],bool(r['type']=='TOTAL_ASSETS' and r['id'].startswith('auto:')),r['created_at'],r['id']))
    orders=defaultdict(int);opening=set(); instrument_ids=set()
    def next_order(a,day):orders[(a,day)]+=10;return orders[(a,day)]
    def instrument(p):
        i=p['instrument'];iid=i['id']
        if iid not in instrument_ids:
            market='SH' if i['kind']=='LISTED' and ':1:' in iid else ('SZ' if i['kind']=='LISTED' else '')
            batch['instruments'].append(dict(id=iid,code=i['code'],name=i['name'],kind=i['kind'],currency='CNY',market=market));instrument_ids.add(iid)
        return iid
    for a in accounts:
        if not any(r['asset_id']==a['id'] and r['type']=='TOTAL_ASSETS' for r in records):
            raise ValueError(f'Asset without opening valuation: {a["name"]}')
        batch['accounts'].append(dict(id=a['id'],name=a['name'],currency='CNY',valuation_mode='manual',holdings_complete=False))
    for r in records:
        aid=r['asset_id'];kind='VALUATION' if r['type']=='TOTAL_ASSETS' else 'CASH_FLOW'
        if r['type'] not in ('TOTAL_ASSETS','TRANSFER'):raise ValueError('Unknown legacy record type')
        if kind=='VALUATION' and aid not in opening:kind='OPENING';opening.add(aid)
        batch['events'].append(dict(id='legacy:'+r['id'],account_id=aid,date=r['business_date'],order=next_order(aid,r['business_date']),kind=kind,amount=r['amount'],source_id=sid,note=r.get('note') or '',legacy_created_at=r['created_at'],transfer_id=r.get('link_id')))
    if 'holding_books' in tables:
        for row in src.execute('select * from holding_books'):
            book=json.loads(row['data']);positions={p['id']:p for p in book['positions']}
            if len({p['instrument']['id'] for p in positions.values()})!=len(positions):
                raise ValueError('Multiple channels for same instrument: normalize into separate accounts or aggregate confirmed initial shares before import')
            for p in positions.values():instrument(p)
            for o in sorted(book['operations'],key=lambda o:(o['date'],o['order'],o['id'])):
                kind='POSITION_OPEN' if o['kind']=='INITIAL' else o['kind']
                e=dict(id='holding:'+o['id'],account_id=book['assetId'],date=o['date'],order=next_order(book['assetId'],o['date']),kind=kind,instrument_id=positions[o['positionId']]['instrument']['id'],shares=o['shares'],source_id=sid,note=o.get('note',''))
                if kind in ('BUY','SELL'):
                    # Legacy amount is the actual cash paid/received, already including fees.
                    e.update(amount=o['amount'],fee='0');e['note']+='；迁移实际收付金额，费用已包含'
                batch['events'].append(e)
            if book.get('overrides'):
                batch['pending'].append(dict(id='legacy-rights:'+book['assetId'],source_id=sid,reason='旧账本有权益确认记录，需核对分红/再投/折算事件后开启自动估值',extracted=book['overrides']))
    if 'market_quotes' in tables:
        for row in src.execute('select * from market_quotes'):
            q=json.loads(row['data'])
            if q['instrumentId'] not in instrument_ids:continue
            batch['quotes'].append(dict(id='legacy-quote:'+q['instrumentId']+':'+q['date'],instrument_id=q['instrumentId'],date=q['date'],price=q['value'],provider=q['source'],source_id=sid,fetched_at=q['fetchedAt']))
    # Some older databases contain only snapshot prices, not a quote cache.
    if 'valuation_snapshots' in tables:
        existing={(q['instrument_id'],q['date']) for q in batch['quotes']}
        for row in src.execute('select * from valuation_snapshots order by date'):
            s=json.loads(row['data'])
            for line in s['lines']:
                match=next((i for i in batch['instruments'] if i['code']==line['code']),None)
                if match and line.get('price') and line.get('priceDate') and (match['id'],line['priceDate']) not in existing:
                    batch['quotes'].append(dict(id='legacy-quote:'+match['id']+':'+line['priceDate'],instrument_id=match['id'],date=line['priceDate'],price=line['price'],provider=line.get('source') or '旧账本快照',source_id=sid,fetched_at=source['imported_at']))
                    existing.add((match['id'],line['priceDate']))
    src.close()
    # Re-import the same factual IDs even if SQLite container bytes changed.
    prior=ledger.load(root)
    prior_accounts={a['id']:a for a in prior['accounts']}
    for index,a in enumerate(batch['accounts']):
        if a['id'] in prior_accounts:
            if prior_accounts[a['id']]['currency']!=a['currency']:raise ValueError('Account currency changed across imports')
            batch['accounts'][index]=prior_accounts[a['id']]
    prior_by_id={e['id']:e for e in prior['events']}
    for e in batch['events']:
        old=prior_by_id.get(e['id'])
        if old and {k:v for k,v in e.items() if k!='source_id'}=={k:v for k,v in old.items() if k!='source_id'}:e['source_id']=old['source_id']
    prior_quotes={q['id']:q for q in prior['quotes']}
    for index,q in enumerate(batch['quotes']):
        old=prior_quotes.get(q['id'])
        if old and {k:v for k,v in q.items() if k!='source_id'}=={k:v for k,v in old.items() if k!='source_id'}:batch['quotes'][index]=old
    return ledger.apply_batch(root,batch)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--db',required=True);a=p.parse_args()
    try:print(json.dumps(migrate(a.root,a.db),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,sqlite3.Error,OSError) as e:p.exit(1,str(e)+'\n')
