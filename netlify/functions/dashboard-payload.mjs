import {getStore} from '@netlify/blobs';
export default async()=>{const value=await getStore({name:'investment-dashboard',consistency:'strong'}).get('current',{type:'json'});return Response.json(value?.payload||null,{status:value?200:503,headers:{'Cache-Control':'no-store'}})};
