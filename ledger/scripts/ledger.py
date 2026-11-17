#!/usr/bin/env python3
"""Persistent personal ledger. Python standard library only."""
import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager

SKILL = Path(__file__).resolve().parents[1]
COLLECTIONS = ('accounts', 'instruments', 'events', 'quotes', 'sources', 'pending')
KINDS = {'OPENING','CASH_FLOW','VALUATION','POSITION_OPEN','BUY','SELL','CASH_BALANCE','DIVIDEND_CASH','DIVIDEND_REINVEST','SPLIT','FEE','VOID','BASIS_CHECKPOINT'}
POSITION_KINDS = {'POSITION_OPEN','BUY','SELL','DIVIDEND_REINVEST','SPLIT'}

def today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def decimal(v):
    if not isinstance(v, str) or not re.fullmatch(r'-?\d+(\.\d+)?', v):
        raise ValueError(f'Expected decimal string, got {v!r}')
    n = Decimal(v)
    if not n.is_finite() or abs(n) > Decimal('1e18'):
        raise ValueError('Amount out of supported range')
    return n

def money(v):
    return str(v.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def date(v):
    if not isinstance(v, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
        raise ValueError(f'Invalid date: {v!r}')
    dt.date.fromisoformat(v)
    if v > today():
        raise ValueError(f'Future fact: {v}')
    return v

def event_key(e):
    return e['date'], e['order'], e['id']

def boundary(e):
    return e['date'], e['order']

def blank():
    return dict(format='personal-investment-ledger', version=2, revision=0,
                **{k: [] for k in COLLECTIONS})

def load(root):
    p = Path(root)/'ledger.json'
    return json.loads(p.read_text()) if p.exists() else blank()

@contextmanager
def lock(root):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    p = root/'ledger.lock'
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f'Ledger locked; inspect active writer before removing {p}')
    try:
        os.write(fd, f'{os.getpid()} {now()}'.encode()); os.close(fd)
        yield
    finally:
        p.unlink(missing_ok=True)

def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def save(root, data):
    root = Path(root); p = root/'ledger.json'
    if p.exists():
        b = root/'backups'; b.mkdir(exist_ok=True)
        shutil.copy2(p, b/f'{data["revision"]-1:06}-{uuid.uuid4().hex[:8]}.json')
    atomic_json(p, data)

def active_events(data, as_of=None):
    # Apply corrections at their effective date, preserving historical as-of behavior.
    ee = [e for e in data['events'] if as_of is None or e['date'] <= as_of]
    removed = {e[k] for e in ee for k in ('supersedes','voids') if e.get(k)}
    return sorted([e for e in ee if e['id'] not in removed and e['kind'] != 'VOID'], key=event_key)

def replay(data, account_id, as_of):
    shares = {}; cash = None; last_position_date = {}
    for e in active_events(data, as_of):
        if e['account_id'] != account_id:
            continue
        k = e['kind']; inst = e.get('instrument_id'); amount = decimal(e.get('amount','0'))
        if k in POSITION_KINDS:
            n = decimal(e['shares'])
            if k == 'POSITION_OPEN':
                shares[inst] = n
            else:
                shares[inst] = shares.get(inst, Decimal(0)) + (-n if k == 'SELL' else n)
            if shares[inst] < 0:
                raise ValueError(f'Negative shares at {e["id"]}')
            last_position_date[inst] = e['date']
        if k == 'CASH_BALANCE':
            cash = amount
        elif cash is not None:
            if k in ('CASH_FLOW','DIVIDEND_CASH'): cash += amount
            elif k == 'BUY': cash -= amount + decimal(e.get('fee','0'))
            elif k == 'SELL': cash += amount - decimal(e.get('fee','0'))
            elif k == 'FEE': cash -= amount
    return shares, cash, last_position_date

def validate(data):
    if data.get('format') != 'personal-investment-ledger' or data.get('version') not in (1,2):
        raise ValueError('Unsupported ledger format/version')
    for key in COLLECTIONS:
        if not isinstance(data.get(key), list): raise ValueError(f'{key} must be array')
        ids = [v.get('id') for v in data[key]]
        if any(not isinstance(i,str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError(f'Missing/duplicate IDs: {key}')
    accounts = {a['id']:a for a in data['accounts']}
    instruments = {i['id']:i for i in data['instruments']}
    sources = {s['id'] for s in data['sources']}
    for a in accounts.values():
        if not a.get('name') or not re.fullmatch('[A-Z]{3}',a.get('currency','')): raise ValueError('Invalid account identity')
        if a.get('valuation_mode') not in ('manual','holdings') or not isinstance(a.get('holdings_complete'),bool): raise ValueError('Invalid account valuation configuration')
        if not isinstance(a.get('archived',False),bool):raise ValueError('archived must be boolean')
    for i in instruments.values():
        if not isinstance(i.get('code'),str) or not i['code'] or not i.get('name'): raise ValueError('Instrument code/name required as strings')
        if i.get('kind') not in ('LISTED','FUND','OTHER') or not re.fullmatch('[A-Z]{3}',i.get('currency','')): raise ValueError('Invalid instrument')
        if i.get('market','') not in ('','SH','SZ'): raise ValueError('Invalid market')
    byid = {e['id']:e for e in data['events']}; targeted = set()
    for e in data['events']:
        if e.get('account_id') not in accounts or e.get('kind') not in KINDS: raise ValueError(f'Invalid event: {e["id"]}')
        date(e['date'])
        if type(e.get('order')) is not int: raise ValueError('Event order must be integer')
        if e.get('source_id') not in sources: raise ValueError(f'Missing event source: {e["id"]}')
        k=e['kind']
        if k=='BASIS_CHECKPOINT':
            if data['version']<2:raise ValueError('Basis checkpoints require schema v2')
            expected=decimal(e['opening_amount'])+decimal(e['gross_in'])-decimal(e['gross_out'])
            if decimal(e['gross_in'])<0 or decimal(e['gross_out'])<0 or expected!=decimal(e['amount']):raise ValueError('Basis checkpoint does not reconcile')
        if k in POSITION_KINDS:
            if e.get('instrument_id') not in instruments: raise ValueError('Unknown instrument')
            if instruments[e['instrument_id']]['currency'] != accounts[e['account_id']]['currency']: raise ValueError('Instrument/account currency mismatch')
            n=decimal(e['shares'])
            if (k=='POSITION_OPEN' and n<0) or (k in ('BUY','SELL','DIVIDEND_REINVEST') and n<=0) or (k=='SPLIT' and n==0): raise ValueError('Invalid share quantity')
        if k in ('OPENING','CASH_FLOW','VALUATION','BUY','SELL','CASH_BALANCE','DIVIDEND_CASH','FEE'):
            n=decimal(e['amount'])
            if k in ('OPENING','VALUATION') and n<0: raise ValueError('Negative assets')
            if k in ('BUY','SELL','DIVIDEND_CASH','FEE') and n<=0: raise ValueError('Positive amount required')
        if k in ('BUY','SELL') and decimal(e.get('fee','0'))<0: raise ValueError('Negative fee')
        if e.get('supersedes') and e.get('voids'): raise ValueError('Ambiguous correction')
        if k=='VOID' and not e.get('voids'): raise ValueError('VOID requires voids')
        if k!='VOID' and e.get('voids'): raise ValueError('Only VOID may void an event')
        for field in ('supersedes','voids'):
            t=e.get(field)
            if not t: continue
            if t not in byid or t==e['id'] or t in targeted: raise ValueError('Invalid/branched correction target')
            target=byid[t]
            if target['account_id']!=e['account_id'] or boundary(target)>boundary(e): raise ValueError('Correction cannot cross accounts or precede target')
            # Replacement changes the same economic event, not its effective date.
            if field=='supersedes' and (target['date']!=e['date'] or target['kind']!=k): raise ValueError('Replacement must preserve effective date and kind; void then add for other corrections')
            targeted.add(t)
            seen={e['id']}; cur=t
            while cur:
                if cur in seen: raise ValueError('Correction cycle')
                seen.add(cur); cur=byid[cur].get('supersedes') or byid[cur].get('voids')
    effective=active_events(data)
    slots=set()
    for e in effective:
        slot=(e['account_id'],e['date'],e['order'])
        if slot in slots: raise ValueError(f'Ambiguous same-day order at {e["id"]}')
        slots.add(slot)
    for a in accounts:
        ee=[e for e in effective if e['account_id']==a]
        openings=[e for e in ee if e['kind']=='OPENING']
        if len(openings)!=1: raise ValueError(f'Account {a} requires exactly one OPENING')
        if any(boundary(e)<boundary(openings[0]) for e in ee): raise ValueError('Event before opening')
        replay(data,a,today())
    for q in data['quotes']:
        if q.get('instrument_id') not in instruments or q.get('source_id') not in sources: raise ValueError('Unknown quote instrument/source')
        date(q['date'])
        if decimal(q['price'])<=0 or not q.get('provider') or not q.get('fetched_at'): raise ValueError('Invalid quote')
    for p in data['pending']:
        if p.get('source_id') not in sources or not p.get('reason'): raise ValueError('Invalid pending source/reason')
        if p.get('resolved_by') and p['resolved_by'] not in byid: raise ValueError('Pending resolution references missing event')
    return True

def fingerprint(e):
    values=[]
    for k in ('account_id','date','kind','instrument_id','shares','amount','fee'):
        v=e.get(k,'0' if k=='fee' else None)
        values.append(decimal(v).normalize() if k in ('shares','amount','fee') and v is not None else v)
    return tuple(values)

def validate_storage(root,data):
    validate(data);root=Path(root).resolve()
    for source in data['sources']:
        p=(root/source['path']).resolve()
        if data.get('authority')=='netlify-blobs' and source.get('original_retention')=='optional' and not p.exists():
            continue
        if not p.is_relative_to(root/'sources') or not p.is_file():raise ValueError('Missing/invalid archived source '+source['id'])
        if hashlib.sha256(p.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('Archived source hash mismatch '+source['id'])
    return True

def apply_batch(root,batch,dry_run=False):
    allowed=set(COLLECTIONS)|{'account_updates','pending_updates','allow_similar'}
    if set(batch)-allowed: raise ValueError(f'Unknown batch fields: {set(batch)-allowed}')
    with lock(root):
        old=load(root); data=copy.deepcopy(old); counts={'added':{},'skipped':{},'updated':0}
        for key in COLLECTIONS:
            existing={v['id']:v for v in data[key]}; additions=batch.get(key,[])
            if not isinstance(additions,list): raise ValueError(f'{key} must be array')
            for item in additions:
                item=copy.deepcopy(item)
                if not isinstance(item,dict) or not item.get('id'): raise ValueError(f'{key} entry requires id')
                prior=existing.get(item['id'])
                if prior is not None:
                    # Evidence can differ when the same broker event is imported from another document.
                    comparable=lambda v:{k:x for k,x in v.items() if k not in ('source_id','note')}
                    if prior!=item and not(key=='events' and comparable(prior)==comparable(item)):
                        raise ValueError(f'ID conflict {key}:{item["id"]}')
                    counts['skipped'][key]=counts['skipped'].get(key,0)+1; continue
                if key=='sources':
                    p=Path(root)/item.get('path','')
                    if not p.resolve().is_relative_to((Path(root)/'sources').resolve()) or not p.is_file(): raise ValueError('Source must be archived under sources/')
                    if hashlib.sha256(p.read_bytes()).hexdigest()!=item.get('sha256'): raise ValueError('Source hash mismatch')
                if key=='events' and not batch.get('allow_similar') and not item.get('supersedes') and item.get('kind')!='VOID':
                    candidates=[v['id'] for v in active_events(data) if fingerprint(v)==fingerprint(item)]
                    if candidates: raise ValueError('duplicate_candidates: '+json.dumps(candidates))
                existing[item['id']]=item; data[key].append(item); counts['added'][key]=counts['added'].get(key,0)+1
        for update in batch.get('account_updates',[]):
            if set(update)-{'id','name','valuation_mode','holdings_complete','archived','archived_at'}: raise ValueError('Unsupported account update')
            a=next((a for a in data['accounts'] if a['id']==update['id']),None)
            if a is None: raise ValueError('Unknown account update')
            if any(a.get(k)!=v for k,v in update.items()): a.update(update); counts['updated']+=1
        for update in batch.get('pending_updates',[]):
            if set(update)-{'id','resolved_by'}: raise ValueError('Unsupported pending update')
            p=next((p for p in data['pending'] if p['id']==update['id']),None)
            if p is None: raise ValueError('Unknown pending update')
            if p.get('resolved_by')!=update.get('resolved_by'): p.update(update); counts['updated']+=1
        if any(e['kind']=='BASIS_CHECKPOINT' for e in data['events']):data['version']=2
        validate(data)
        changed=any(counts['added'].values()) or counts['updated']
        if changed and not dry_run:
            data['revision']=old['revision']+1; data['updated_at']=now(); save(root,data)
        counts.update(dry_run=dry_run,revision=data['revision'],pending=sum(not p.get('resolved_by') for p in data['pending']))
        return counts

def archive_source(root,path):
    path=Path(path).resolve(); raw=path.read_bytes(); digest=hashlib.sha256(raw).hexdigest()
    root=Path(root); dest=root/'sources'/digest[:16]/path.name; dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists(): dest.write_bytes(raw)
    sid='sha256:'+digest
    existing=next((s for s in load(root)['sources'] if s['id']==sid),None)
    if existing: return existing
    return {'id':sid,'sha256':digest,'filename':path.name,'path':str(dest.relative_to(root)),'imported_at':now()}

def overview(data,as_of=None):
    as_of=as_of or today(); date(as_of)
    effective=active_events(data,as_of); instruments={i['id']:i for i in data['instruments']}
    out=[]; positions=[]; totals={}
    for a in data['accounts']:
        ee=[e for e in effective if e['account_id']==a['id']]
        vv=[e for e in ee if e['kind'] in ('OPENING','VALUATION')]
        if not vv: continue
        opening=next(e for e in ee if e['kind']=='OPENING'); val=vv[-1]
        shares,cash,pdates=replay(data,a['id'],as_of); securities=Decimal(0); missing=[]; stale=[]; price_dates=[]
        for iid,n in shares.items():
            if n==0: continue
            ii=instruments[iid]; qq=sorted([q for q in data['quotes'] if q['instrument_id']==iid and q['date']<=as_of],key=lambda q:(q['date'],q['fetched_at'],q['id']))
            q=qq[-1] if qq else None
            v=(n*decimal(q['price'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if q else None
            if q:
                securities+=v;price_dates.append(q['date'])
                if (dt.date.fromisoformat(as_of)-dt.date.fromisoformat(q['date'])).days>7: stale.append(ii['code'])
            else: missing.append(ii['code'])
            positions.append({'account':a['name'],'currency':a['currency'],'code':ii['code'],'name':ii['name'],'shares':str(n),'shares_date':pdates[iid],'price':q['price'] if q else None,'price_date':q['date'] if q else None,'value':money(v) if v is not None else None,'provider':q['provider'] if q else None})
        issues=[]
        if not a['holdings_complete']: issues.append('持仓范围未确认完整')
        if cash is None: issues.append('现金余额未知')
        if missing: issues.append('缺价: '+','.join(missing))
        if stale: issues.append('过期报价: '+','.join(stale))
        automatic=a['valuation_mode']=='holdings' and a['holdings_complete'] and cash is not None and not missing
        cutoff=(as_of,sys.maxsize) if automatic else boundary(val)
        flows=[e for e in ee if e['kind']=='CASH_FLOW' and boundary(e)<=cutoff]
        net=decimal(opening['amount'])+sum((decimal(e['amount']) for e in flows),Decimal(0))
        checkpoints=[e for e in ee if e['kind']=='BASIS_CHECKPOINT' and boundary(e)<=cutoff]
        if checkpoints:
            checkpoint=checkpoints[-1]
            net=decimal(checkpoint['amount'])+sum((decimal(e['amount']) for e in flows if boundary(e)>boundary(checkpoint)),Decimal(0))
            issues.append('净投入已按对账汇总校准，历史差额待核对' if any(not p.get('resolved_by') and p.get('extracted',{}).get('checkpoint_id')==checkpoint['id'] for p in data['pending']) else '净投入采用已核对汇总')
        value=securities+cash if automatic else decimal(val['amount'])
        profit=value-net
        row={'name':a['name'],'currency':a['currency'],'value':money(value),'basis':money(net),'profit':money(profit),'profit_rate':str(profit/net) if net>0 else None,'as_of':as_of if automatic else val['date'],'mode':'自动估值' if automatic else '人工估值','securities':money(securities),'cash':money(cash) if cash is not None else None,'issues':issues,'price_dates':sorted(set(price_dates)),'unvalued_flow':money(sum((decimal(e['amount']) for e in ee if e['kind']=='CASH_FLOW' and boundary(e)>cutoff),Decimal(0)))}
        out.append(row)
        row.update(account_id=a['id'],archived=a.get('archived',False),archived_at=a.get('archived_at'))
        t=totals.setdefault(a['currency'],{'value':Decimal(0),'basis':Decimal(0),'profit':Decimal(0)})
        for k in t: t[k]+=decimal(row[k])
    return {'as_of':as_of,'accounts':out,'positions':positions,'totals':{c:{k:money(v) for k,v in t.items()} for c,t in totals.items()},'pending_count':sum(not p.get('resolved_by') for p in data['pending']),'event_count':len(effective),'revision':data['revision']}

def dashboard(root,as_of=None,public=False,disclose='redacted'):
    data=load(root);validate(data);as_of=as_of or today();summary=overview(data,as_of)
    days=sorted({e['date'] for e in active_events(data,as_of) if e['kind'] in ('OPENING','VALUATION')})
    if days and days[-1]!=as_of: days.append(as_of)
    curves={};valuation_cache={}
    for day in days:
        valuation_cache[day]=overview(data,day)
        for currency,total in valuation_cache[day]['totals'].items(): curves.setdefault(currency,[]).append({'date':day,**total})
    visible_events=[{'account':next(a['name'] for a in data['accounts'] if a['id']==e['account_id']),'currency':next(a['currency'] for a in data['accounts'] if a['id']==e['account_id']),'date':e['date'],'kind':e['kind'],'instrument':next((i['name'] for i in data['instruments'] if i['id']==e.get('instrument_id')),''),'amount':e.get('amount'),'shares':e.get('shares')} for e in active_events(data,as_of)]
    payload={**summary,'curves':curves,'events':visible_events,'pending':[{'reason':p['reason']} for p in data['pending'] if not p.get('resolved_by')],'disclosure':'local' if not public else disclose}
    if not public or disclose=='full':
        import performance
        payload['performance']=performance.build(data,as_of,valuation_cache)
        if public:
            for i,scope in enumerate(payload['performance']):scope['id']='scope:'+str(i)
    if public:
        for account in payload['accounts']:account.pop('account_id',None)
    if public and disclose!='full':
        payload={k:payload[k] for k in ('as_of','totals','curves','event_count','pending_count','disclosure')}
        if disclose=='redacted':
            payload['totals']={c:{'profit_rate':str(decimal(t['profit'])/decimal(t['basis'])) if decimal(t['basis'])>0 else None} for c,t in summary['totals'].items()}
            payload['curves']={}
            for c,points in curves.items():
                first=next((decimal(p['value']) for p in points if decimal(p['value'])!=0),None)
                payload['curves'][c]=[{'date':p['date'],'index':str(decimal(p['value'])/first*100) if first else None} for p in points]
    encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    template=(SKILL/'assets/dashboard.html').read_text()
    content=template.replace('__LEDGER_DATA__',encoded)
    target=Path(root)/('public' if public else 'dashboard')/'index.html';target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.html-',dir=target.parent)
    with os.fdopen(fd,'w') as f:f.write(content)
    os.replace(tmp,target)
    return str(target.resolve())

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    settings=json.loads((SKILL/'settings.json').read_text()) if (SKILL/'settings.json').exists() else {}
    p.add_argument('--root',default=settings.get('data_dir',str(Path.home()/'Documents/PersonalInvestment')))
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('validate')
    a=sub.add_parser('apply');a.add_argument('--batch',required=True);a.add_argument('--dry-run',action='store_true')
    a=sub.add_parser('source');a.add_argument('file')
    a=sub.add_parser('summary');a.add_argument('--as-of')
    a=sub.add_parser('dashboard');a.add_argument('--as-of');a.add_argument('--public',action='store_true');a.add_argument('--disclose',choices=['redacted','amounts','full'],default='redacted')
    return p

def main():
    args=parser().parse_args();root=Path(args.root).expanduser().resolve()
    try:
        if args.command=='init':
            with lock(root):
                if not(root/'ledger.json').exists():save(root,blank())
            result={'root':str(root),'revision':load(root)['revision']}
        elif args.command=='source':
            source=archive_source(root,args.file);apply_batch(root,{'sources':[source]});result={'source_id':source['id'],'filename':source['filename']}
        elif args.command=='apply':result=apply_batch(root,json.loads(Path(args.batch).read_text()),args.dry_run)
        elif args.command=='validate':validate_storage(root,load(root));result={'valid':True,'revision':load(root)['revision']}
        elif args.command=='summary':validate(load(root));result=overview(load(root),args.as_of)
        else:result={'path':dashboard(root,args.as_of,args.public,args.disclose)}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError,OSError,InvalidOperation) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
