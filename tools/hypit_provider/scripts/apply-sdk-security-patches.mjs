/** Reproducible metadata-only compatibility patch for fixed security dependencies.
 * SDK/Provider executable source is unchanged. Unknown upstream manifests fail closed.
 */
import {readFileSync,writeFileSync,renameSync,unlinkSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
const sha=text=>createHash('sha256').update(text).digest('hex');
const patches=[
  ['@hypit/media-local','sharp','0.35.4','0.35.5','37acbd523d037b2596909a46296c02218d0f95df3c69165dbe55ebb082fd522d'],
  ['@hypit/browser-capture','sharp','0.35.4','0.35.5','4407b3d54af91ec3ce5a22fa7ba44457ec187f9714a90d1bd774bfcb2fff57d4'],
  ['tsx','esbuild','~0.27.0','0.28.1','6e764b21991c21595441f82387c1451ccd10a3e7a3f0973cc5e3649a12dea7d2'],
];
const evidence=[];
for(const [name,dependency,before,after,originalHash] of patches){
  const file=resolve('node_modules',name,'package.json');
  const raw=readFileSync(file,'utf8'),document=JSON.parse(raw);
  const current=document.dependencies?.[dependency];
  if(current!==before && current!==after) throw new Error(`Unknown ${name} dependency baseline`);
  document.dependencies[dependency]=before;
  const ending=name==='tsx'?'':'\n';
  const original=JSON.stringify(document,null,2)+ending;
  if(sha(original)!==originalHash) throw new Error(`Unknown ${name} manifest hash; no security patch applied`);
  document.dependencies[dependency]=after;
  const patched=JSON.stringify(document,null,2)+ending;
  if(raw!==patched){
    const temporary=file+`.seedance-${process.pid}.tmp`;
    try {writeFileSync(temporary,patched,{flag:'wx'});renameSync(temporary,file);}
    finally {try{unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
  }
  evidence.push({package:name,dependency,upstream_requirement:before,secured_requirement:after,
    upstream_sha256:originalHash,patched_sha256:sha(patched),scope:'dependency manifest only; executable source unchanged'});
}
writeFileSync('SDK_SECURITY_PATCHES.json',JSON.stringify({hypit:'0.3.1',patches:evidence},null,2)+'\n');
console.log('Verified three dependency metadata patches for secured sharp/esbuild versions');
