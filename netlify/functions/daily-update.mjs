import {getStore} from '@netlify/blobs';
import {commit} from '../lib/store.mjs';
import {update,unseal} from '../lib/runtime.mjs';
export default async()=>{
 const password=process.env.DASHBOARD_PASSWORD;if(!password)throw Error('Missing dashboard key');
 const store=getStore({name:'investment-dashboard',consistency:'strong'});
 const snapshot=await store.getWithMetadata('current',{type:'json'});const current=snapshot?.data;if(!current)throw Error('Missing initial local snapshot');
 const result=await update(JSON.parse(unseal(current.state,password)),password);
 if(!result.report.updated.length)throw Error('No successful quotes; preserving previous dashboard');
 for(const [url,b64] of Object.entries(result.responses)){const {createHash}=await import('node:crypto');await store.set('sources/'+createHash('sha256').update(Buffer.from(b64,'base64')).digest('hex'),Buffer.from(b64,'base64'))}
 await store.setJSON('history/'+result.report.completed_at,{...result,responses:undefined});
 await commit(store,snapshot.etag,{state:result.state,payload:result.payload,report:result.report});
 console.log(JSON.stringify(result.report));return new Response('Updated');
};
export const config={schedule:'0 0 * * *'};
