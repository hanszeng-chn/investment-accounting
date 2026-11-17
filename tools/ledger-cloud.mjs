import {readFileSync,writeFileSync,mkdirSync,existsSync,copyFileSync} from 'node:fs';
import {homedir} from 'node:os';import {resolve} from 'node:path';import {spawnSync} from 'node:child_process';
import {getStore} from '@netlify/blobs';import {encrypt} from './encrypted-dashboard/build.mjs';import {unseal} from '../netlify/lib/runtime.mjs';import {commit,digest} from '../netlify/lib/store.mjs';
const root=resolve(process.env.LEDGER_ROOT||'data/personal-investment'),config=JSON.parse(readFileSync(homedir()+'/Library/Preferences/netlify/config.json'));
const store=getStore({name:'investment-dashboard',siteID:'7998c9f7-a8f6-4082-8e74-0f784b450ae6',token:config.users[config.userId].auth.token,consistency:'strong'});
const password=readFileSync(resolve('data/personal-investment/secrets/dashboard-password.txt'),'utf8').trimEnd();
const action=process.argv[2];const snapshot=await store.getWithMetadata('current',{type:'json'});if(!snapshot)throw Error('Missing authoritative ledger');
const remote=JSON.parse(unseal(snapshot.data.state,password));
const local=existsSync(root+'/ledger.json')?JSON.parse(readFileSync(root+'/ledger.json')):null;const stamp=new Date().toISOString().replaceAll(':','-');mkdirSync(root+'/backups',{recursive:true});
function python(...args){const r=spawnSync('python3',['ledger/scripts/ledger.py','--root',root,...args],{stdio:'inherit'});if(r.status!==0)throw Error('Ledger validation/build failed')}
function save(data){if(local)copyFileSync(root+'/ledger.json',root+'/backups/pre-cloud-'+stamp+'.json');writeFileSync(root+'/ledger.json',JSON.stringify(data,null,2)+'\n');writeFileSync(root+'/.blobs-base.json',JSON.stringify({etag:snapshot.etag,hash:digest(data),revision:data.revision}));}
if(action==='audit'){
 const counts=d=>Object.fromEntries(['accounts','instruments','events','quotes','sources','pending'].map(k=>[k,d[k].length]));
 const diffs={};for(const k of ['accounts','instruments','events','pending']){const a=new Map(local[k].map(v=>[v.id,v])),b=new Map(remote[k].map(v=>[v.id,v]));diffs[k]={localOnly:[...a.keys()].filter(i=>!b.has(i)),remoteOnly:[...b.keys()].filter(i=>!a.has(i)),conflicts:[...a.keys()].filter(i=>b.has(i)&&digest(a.get(i))!==digest(b.get(i)))};}
 console.log(JSON.stringify({local:{revision:local.revision,...counts(local)},remote:{revision:remote.revision,...counts(remote)},diffs,pending:remote.pending},null,2));
}else if(action==='pull'||action==='migrate'){
 if(action==='migrate')for(const k of ['accounts','instruments','events','quotes','sources','pending'])for(const v of local[k]){const r=remote[k].find(x=>x.id===v.id);if(!r)throw Error('Local-only fact found: '+k+' '+v.id);if(digest(v)!==digest(r))throw Error('Conflicting fact: '+k+' '+v.id)}
 if(action==='pull'&&existsSync(root+'/.blobs-base.json')){const base=JSON.parse(readFileSync(root+'/.blobs-base.json'));if(base.hash!==digest(local))throw Error('Uncommitted local changes; push first')}
 if(action==='migrate'){remote.authority='netlify-blobs';for(const source of remote.sources)source.original_retention='optional';}
 save(remote);python('validate');python('dashboard');
 if(action==='migrate'){
  remote.authority='netlify-blobs';remote.revision+=1;remote.updated_at=new Date().toISOString();
  for(const source of remote.sources)source.original_retention='optional';
  writeFileSync(root+'/ledger.json',JSON.stringify(remote,null,2)+'\n');python('validate');python('dashboard');
  const value={state:encrypt(JSON.stringify(remote),password),payload:encrypt(readFileSync(root+'/dashboard/index.html'),password),report:{migrated_at:remote.updated_at,revision:remote.revision,authority:'netlify-blobs'}};
  await store.setJSON('versions/pre-migration-'+stamp,snapshot.data,{onlyIfNew:true});await commit(store,snapshot.etag,value);
  const check=await store.getWithMetadata('current',{type:'json'});if(digest(JSON.parse(unseal(check.data.state,password)))!==digest(remote))throw Error('Read-back mismatch');
  writeFileSync(root+'/.blobs-base.json',JSON.stringify({etag:check.etag,hash:digest(remote),revision:remote.revision}));
 }
 console.log(JSON.stringify({action,revision:remote.revision,verified:true}));
}else if(action==='push'){
 const base=JSON.parse(readFileSync(root+'/.blobs-base.json'));if(base.etag!==snapshot.etag)throw Error('Cloud changed; pull and replay changes');
 python('validate');python('dashboard');
 await commit(store,base.etag,{state:encrypt(JSON.stringify(local),password),payload:encrypt(readFileSync(root+'/dashboard/index.html'),password),report:{revision:local.revision,committed_at:new Date().toISOString()}});
 const check=await store.getWithMetadata('current',{type:'json'});if(digest(JSON.parse(unseal(check.data.state,password)))!==digest(local))throw Error('Read-back mismatch');writeFileSync(root+'/.blobs-base.json',JSON.stringify({etag:check.etag,hash:digest(local),revision:local.revision}));console.log('Committed and verified revision '+local.revision);
}else if(action==='analyze'){
 const dir=root+'/exports/analysis-'+remote.revision;mkdirSync(dir,{recursive:true});writeFileSync(dir+'/ledger.json',JSON.stringify(remote,null,2));
 const cell=v=>'\"'+String(v==null?'':typeof v==='object'?JSON.stringify(v):v).replaceAll('\"','\"\"')+'\"';
 for(const name of ['accounts','instruments','events','quotes','sources','pending']){const rows=remote[name],cols=[...new Set(rows.flatMap(v=>Object.keys(v)))];writeFileSync(dir+'/'+name+'.csv','\uFEFF'+[cols.map(cell).join(','),...rows.map(row=>cols.map(k=>cell(row[k])).join(','))].join('\r\n'));}
 console.log('Structured JSON and CSV exported to '+dir);
}else if(action==='export'){
 const path=root+'/backups/blobs-'+remote.revision+'-'+stamp+'.encrypted.json';writeFileSync(path,JSON.stringify(snapshot.data));console.log(path);
}else throw Error('Usage: node tools/ledger-cloud.mjs audit|migrate|pull|push|export|analyze');
