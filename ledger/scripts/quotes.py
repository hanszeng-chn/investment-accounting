#!/usr/bin/env python3
"""Fetch last published CNY close/NAV, with archived responses and per-symbol failures."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import ledger

def download(url):
    last=None
    for attempt in range(2):
        try:
            req=Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://fund.eastmoney.com/' if 'eastmoney' in url else 'https://gu.qq.com/'})
            with urlopen(req,timeout=15) as r:raw=r.read(8_000_001)
            if len(raw)>8_000_000:raise ValueError('Response too large')
            return raw
        except (OSError,ValueError) as e:
            last=e
            if attempt==0:time.sleep(.25)
    raise ValueError(str(last))

def assignment(text,name):
    m=re.search(r'\b'+re.escape(name)+r'\s*=\s*(\[[\s\S]*?\]|"[^"\r\n]*")\s*;',text)
    if not m:raise ValueError(f'Provider format changed: missing {name}')
    return json.loads(m[1])

def fetch(instrument,as_of):
    code=instrument['code']
    if instrument['currency']!='CNY' or not re.fullmatch(r'\d{6}',code):raise ValueError('Unsupported market/currency; import sourced quote manually')
    if instrument['kind']=='FUND':
        url=f'https://fund.eastmoney.com/pingzhongdata/{code}.js'
        raw=download(url);text=raw.decode('utf-8-sig')
        if assignment(text,'fS_code')!=code:raise ValueError('Fund code mismatch')
        name=assignment(text,'fS_name')
        if '货币' in name:raise ValueError('Money-market fund requires income accounting')
        points=[]
        for p in assignment(text,'Data_netWorthTrend'):
            day=dt.datetime.fromtimestamp(p['x']/1000,dt.timezone(dt.timedelta(hours=8))).date().isoformat()
            if day<=as_of and ledger.decimal(str(p['y']))>0:points.append((day,str(p['y'])))
        provider='天天基金·单位净值'
    elif instrument['kind']=='LISTED':
        if instrument.get('market') not in ('SH','SZ'):raise ValueError('Explicit SH/SZ market required')
        symbol=instrument['market'].lower()+code
        start=(dt.date.fromisoformat(as_of)-dt.timedelta(days=40)).isoformat()
        url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urlencode({'param':f'{symbol},day,{start},{as_of},60,'})
        raw=download(url);obj=json.loads(raw.decode('utf-8'))
        if obj.get('code')!=0:raise ValueError('Provider rejected request')
        payload=obj['data'][symbol];identity=payload.get('qt',{}).get(symbol)
        if not identity or identity[2]!=code:raise ValueError('Listed security code mismatch')
        points=[(p[0],p[2]) for p in payload.get('day',[]) if p[0]<=as_of and ledger.decimal(p[2])>0]
        provider='腾讯证券·未复权收盘价'
    else:raise ValueError('Unsupported instrument kind')
    if not points:raise ValueError('No published quote on/before cutoff')
    day,price=max(points,key=lambda p:p[0]);ledger.date(day)
    china_now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    if instrument['kind']=='LISTED' and day==china_now.date().isoformat() and china_now.hour<15:
        provider='腾讯证券·当日日线最新价（未收盘）'
    return day,price,provider,url,raw

def update(root,as_of):
    ledger.date(as_of);data=ledger.load(root);ledger.validate(data)
    wanted=set()
    for a in data['accounts']:
        shares,_,_=ledger.replay(data,a['id'],as_of)
        wanted.update(i for i,n in shares.items() if n!=0)
    success=[];errors=[]
    with tempfile.TemporaryDirectory() as td:
        for instrument in data['instruments']:
            if instrument['id'] not in wanted:continue
            try:
                day,price,provider,url,raw=fetch(instrument,as_of)
                response=Path(td)/(instrument['code']+'-quote.txt');response.write_bytes(raw)
                source=ledger.archive_source(root,response);source['url']=url
                prior=next((s for s in ledger.load(root)['sources'] if s['id']==source['id']),None)
                if prior:source=prior
                qid='quote:'+hashlib.sha256((instrument['id']+'|'+day+'|'+price+'|'+provider).encode()).hexdigest()
                priorq=next((q for q in ledger.load(root)['quotes'] if q['id']==qid),None)
                q=priorq or dict(id=qid,instrument_id=instrument['id'],date=day,price=price,provider=provider,source_id=source['id'],fetched_at=ledger.now())
                ledger.apply_batch(root,{'sources':[source],'quotes':[q]})
                success.append({'code':instrument['code'],'date':day,'price':price,'provider':provider})
            except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as e:
                errors.append({'code':instrument['code'],'error':str(e)})
    target=ledger.dashboard(root,as_of)
    report={'as_of':as_of,'updated':success,'failed':errors,'dashboard':target}
    ledger.atomic_json(Path(root)/'last-market-update.json',report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--as-of',default=ledger.today());a=p.parse_args()
    try:
        result=update(a.root,a.as_of);print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit(2 if result['failed'] else 0)
    except (ValueError,KeyError,OSError) as e:p.exit(1,str(e)+'\n')
