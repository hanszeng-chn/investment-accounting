import {createHash} from 'node:crypto';
export const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
export async function commit(store,expected,value){
 const id=digest(value);
 await store.setJSON('versions/'+id,value,{onlyIfNew:true});
 const result=await store.setJSON('current',value,{onlyIfMatch:expected});
 if(!result.modified)throw Error('账本已更新，请重新 pull 后重放修改；禁止覆盖');
 return id;
}
