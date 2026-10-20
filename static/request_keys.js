/* Keep an intent key until a complete response is acknowledged, including reloads. */
(() => {
 const routes=new Set(['/api/image/generate','/api/video/direct-generate','/api/video/generate','/api/workflow/analyze','/api/workflow/edit','/api/assistant/chat','/api/hypit/author','/api/hypit/build','/api/hypit/build-paid','/api/bright-tiktok/submit','/api/product-memory/analyze/generate','/api/hot-radar/video/review','/api/hot-radar/scripts/generate','/api/creative-production/plan/generate','/api/creative-production/first-cut/generate']);
 const original=window.fetch.bind(window);
 window.fetch=async function(input,options={}){
  const url=new URL(typeof input==='string'?input:input.url,location.href);
  if(url.origin!==location.origin || String(options.method||'GET').toUpperCase()!=='POST' || !routes.has(url.pathname) || typeof options.body!=='string')return original(input,options);
  const headers=new Headers(options.headers||{});
  if(headers.has('Idempotency-Key') || !window.crypto?.subtle)return original(input,options);
  let storageKey,key;
  try{
   const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(url.pathname+'\0'+options.body));
   const hash=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
   storageKey='studio-operation-v1:'+(window.STUDIO_CONTEXT?.workspace||'owner')+':'+hash;
   key=localStorage.getItem(storageKey);
   if(!key){
    let pending=0;for(let i=0;i<localStorage.length;i++)if(localStorage.key(i)?.startsWith('studio-operation-v1:'))pending++;
    if(pending>=100)return original(input,options); // Never evict an unacknowledged key.
    key=crypto.randomUUID();localStorage.setItem(storageKey,key);
   }
   if(!key || key.length>128)return original(input,options);
  }catch{return original(input,options)} // Backend fingerprint protection remains active.
  headers.set('Idempotency-Key',key);
  const response=await original(input,{...options,headers});
  try{
   const result=await response.clone().json();
   const acknowledged=response.ok && result.ok===true;
   const rejectedBeforeCompletion=response.status>=400 && response.status<500 && response.status!==409;
   if((acknowledged||rejectedBeforeCompletion) && localStorage.getItem(storageKey)===key)localStorage.removeItem(storageKey);
  }catch{} // Incomplete or lost response keeps the key. No automatic retry.
  return response;
 };
})();
