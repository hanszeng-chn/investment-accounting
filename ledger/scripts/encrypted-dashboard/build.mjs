import {readFileSync,writeFileSync,mkdirSync,existsSync,renameSync,readdirSync} from 'node:fs';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes,pbkdf2Sync,createCipheriv} from 'node:crypto';
export function encrypt(html,password){
 if(password.length<8)throw new Error('Use a password of at least 8 characters');
 const salt=randomBytes(32),iv=randomBytes(12),iterations=600000;
 const key=pbkdf2Sync(password,salt,iterations,32,'sha256');
 const cipher=createCipheriv('aes-256-gcm',key,iv);
 const ciphertext=Buffer.concat([cipher.update(html),cipher.final(),cipher.getAuthTag()]);key.fill(0);
 return {version:1,algorithm:'AES-256-GCM',kdf:'PBKDF2-SHA256',iterations,salt:salt.toString('base64'),iv:iv.toString('base64'),ciphertext:ciphertext.toString('base64')};
}
export function build(input,output,password){
 const payload=encrypt(readFileSync(input),password);
 const template=readFileSync(new URL('./unlock.html',import.meta.url),'utf8');
 mkdirSync(output,{recursive:true});
 const allowed=new Set(['index.html','netlify.toml']);
 for(const name of readdirSync(output))if(!allowed.has(name))throw new Error('Unexpected deployment file: '+name);
 // Refuse to publish into a directory containing old plaintext or unrelated assets.
 // Repeated builds replace only known encrypted deployment files.
 const html=template.replace('__ENCRYPTED_PAYLOAD__',JSON.stringify(payload));
 const temporary=join(output,'.encrypted-index.tmp');writeFileSync(temporary,html);renameSync(temporary,join(output,'index.html'));
 writeFileSync(join(output,'netlify.toml'),'[build]\n  publish = "."\n\n[[headers]]\n  for = "/*"\n  [headers.values]\n    Cache-Control = "no-store"\n    X-Robots-Tag = "noindex, nofollow, noarchive"\n    X-Content-Type-Options = "nosniff"\n    Referrer-Policy = "no-referrer"\n');
 return {encrypted:true,algorithm:payload.algorithm,iterations:payload.iterations,output:resolve(output)};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const args=process.argv.slice(2),get=k=>args[args.indexOf(k)+1];
 try{
  for(const key of ['--input','--output','--password-file'])if(!args.includes(key))throw new Error('Required: '+key);
  const passwordFile=resolve(get('--password-file')),output=resolve(get('--output'));
  if(passwordFile===output||passwordFile.startsWith(output+'/'))throw new Error('Password must stay outside deployment directory');
  if(!existsSync(passwordFile)){
   if(!args.includes('--generate-password'))throw new Error('Password file missing');
   mkdirSync(dirname(passwordFile),{recursive:true,mode:0o700});
   writeFileSync(passwordFile,randomBytes(24).toString('base64url')+'\n',{mode:0o600,flag:'wx'});
  }
  console.log(JSON.stringify(build(get('--input'),output,readFileSync(passwordFile,'utf8').trimEnd())));
 }catch(e){console.error(e.message);process.exitCode=1}
}
