import {loadPyodide} from 'pyodide';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {encrypt} from '../../tools/encrypted-dashboard/build.mjs';
import {createDecipheriv,pbkdf2Sync} from 'node:crypto';
export function unseal(p,password){const key=pbkdf2Sync(password,Buffer.from(p.salt,'base64'),p.iterations,32,'sha256'),b=Buffer.from(p.ciphertext,'base64');const d=createDecipheriv('aes-256-gcm',key,Buffer.from(p.iv,'base64'));d.setAuthTag(b.subarray(-16));return Buffer.concat([d.update(b.subarray(0,-16)),d.final()]).toString()}
export async function update(data,password,asOf){
 const py=await loadPyodide(); const fs=py.FS;
 fs.mkdirTree('/app/scripts');fs.mkdirTree('/app/assets');fs.mkdirTree('/data');
 for(const name of ['ledger.py','quotes.py','performance.py'])fs.writeFile('/app/scripts/'+name,readFileSync(resolve('ledger/scripts',name)));
 fs.writeFile('/app/assets/dashboard.html',readFileSync(resolve('ledger/assets/dashboard.html')));
 fs.writeFile('/data/ledger.json',JSON.stringify(data));
 py.runPython("import sys;sys.path.insert(0,'/app/scripts');import ledger,quotes,json,datetime,base64; from pathlib import Path");
 const cutoff=asOf||new Date(Date.now()+8*3600000-86400000).toISOString().slice(0,10);
 py.globals.set('cutoff',cutoff);
 const instruments=JSON.parse(py.runPython(`d=ledger.load('/data')
wanted=set()
for a in d['accounts']:
 shares,_,_=ledger.replay(d,a['id'],cutoff)
 wanted.update(i for i,n in shares.items() if n!=0)
json.dumps([i for i in d['instruments'] if i['id'] in wanted])`));
 const responses={},failed=[];
 await Promise.all(instruments.map(async i=>{try{
 let url;
 if(i.kind==='FUND')url=`https://fund.eastmoney.com/pingzhongdata/${i.code}.js`;
 else {const start=new Date(Date.parse(cutoff)-40*86400000).toISOString().slice(0,10);url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+new URLSearchParams({param:`${i.market.toLowerCase()+i.code},day,${start},${cutoff},60,`})}
 const r=await fetch(url,{signal:AbortSignal.timeout(12000),headers:{'User-Agent':'Mozilla/5.0','Referer':i.kind==='FUND'?'https://fund.eastmoney.com/':'https://gu.qq.com/'}});if(!r.ok)throw Error('HTTP '+r.status);responses[url]=Buffer.from(await r.arrayBuffer()).toString('base64');
 }catch(e){failed.push({code:i.code,error:String(e)})}}));
 py.globals.set('response_json',JSON.stringify(responses));
 const result=JSON.parse(py.runPython(`responses=json.loads(response_json)
quotes.download=lambda url:base64.b64decode(responses[url])
updated=[];failed=[]
for i in d['instruments']:
 if i['id'] not in wanted:continue
 try:
  day,price,provider,url,raw=quotes.fetch(i,cutoff)
  sid='netlify-response:'+ledger.hashlib.sha256(raw).hexdigest()
  if not any(s['id']==sid for s in d['sources']):d['sources'].append(dict(original_retention='optional',id=sid,path='sources/'+sid.split(':')[1]+'.txt',sha256=ledger.hashlib.sha256(raw).hexdigest(),url=url,imported_at=ledger.now(),original_name=i['code']+'-quote.txt'))
  qid='quote:'+ledger.hashlib.sha256((i['id']+'|'+day+'|'+price+'|'+provider).encode()).hexdigest()
  if not any(q['id']==qid for q in d['quotes']):d['quotes'].append(dict(id=qid,instrument_id=i['id'],date=day,price=price,provider=provider,source_id=sid,fetched_at=ledger.now()))
  updated.append(dict(code=i['code'],date=day,price=price))
 except Exception as e:failed.append(dict(code=i['code'],error=str(e)))
d['revision']+=1
d['updated_at']=ledger.now()
ledger.validate(d)
ledger.atomic_json('/data/ledger.json',d)
target=ledger.dashboard('/data',cutoff)
json.dumps(dict(ledger=d,html=Path(target).read_text(),updated=updated,failed=failed,as_of=cutoff))`));
 py.globals.destroy();
 return {state:encrypt(JSON.stringify(result.ledger),password),payload:encrypt(result.html,password),report:{as_of:cutoff,updated:result.updated,failed:result.failed,completed_at:new Date().toISOString()},responses};
}
