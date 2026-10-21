const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('component dialog uses authenticated same-origin API and renders safe text',async()=>{
  const all=[];
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.events={};this.style={};all.push(this);}
    append(...nodes){this.children.push(...nodes);}
    setAttribute(name,value){this[name]=value;}
    addEventListener(name,fn){this.events[name]=fn;}
    replaceChildren(){this.children=[];}
    showModal(){this.open=true;}
    close(){this.open=false;}
  }
  const page=new Element('page');page.classList={contains:()=>true};
  const document={readyState:'complete',hidden:false,createElement:tag=>new Element(tag),
    getElementById:id=>id==='page-settings'?page:null};
  let state={active:'asr',components:[{id:'asr',title:'<unsafe>',version:'pinned',total:100,downloaded:25,state:'downloading',source:'modelscope.cn'}],update_policy:'Locked'};
  const requests=[];
  const fetch=async(url,options)=>{
    requests.push({url,options});
    return {ok:true,json:async()=>({ok:true,data:url==='/api/updates/status'?{state:'BLOCKED'}:state})};
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/desktop_maintenance.js'),'utf8'),
    {document,fetch,setTimeout(){},window:{confirm:()=>false}});
  await new Promise(resolve=>setImmediate(resolve));
  const opener=all.find(e=>e.tag==='button'&&e.textContent==='插件与组件 · 下载中');assert.ok(opener);
  await opener.events.click();
  assert.equal(all.find(e=>e.tag==='dialog').open,true);
  assert.ok(all.some(e=>e.tag==='h4'&&e.textContent==='<unsafe> · 下载中'));
  assert.equal(all.find(e=>e.tag==='progress').value,25);
  const pause=all.find(e=>e.tag==='button'&&e.textContent==='暂停');await pause.events.click();
  const request=requests.find(r=>r.url==='/api/components/cancel');
  assert.deepEqual(JSON.parse(request.options.body),{id:'asr'});
  assert.equal(request.options.method,'POST');
  assert.ok(requests.every(r=>r.url.startsWith('/api/')));
});

test('offline chooser survives polling, cancellation and same-file reselection without duplicate imports',async()=>{
  const all=[],timers=[],requests=[];let confirms=true,releaseImport;
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.events={};this.style={};all.push(this);}
    append(...nodes){this.children.push(...nodes);}
    setAttribute(name,value){this[name]=value;}
    addEventListener(name,fn){this.events[name]=fn;}
    replaceChildren(){this.children=[];}
    showModal(){this.open=true;}
    click(){this.clicks=(this.clicks||0)+1;}
  }
  const page=new Element('page');page.classList={contains:()=>true};
  const document={readyState:'complete',hidden:false,createElement:tag=>new Element(tag),
    getElementById:id=>id==='page-settings'?page:null};
  const state={components:[{id:'asr',title:'ASR',version:'pinned',total:100,state:'not_installed'},
    {id:'browser',title:'Browser',version:'pinned',total:100,state:'not_installed'}],update_policy:'Locked'};
  const fetch=async(url,options)=>{
    requests.push({url,options});
    if(url.startsWith('/api/components/import?'))await new Promise(resolve=>{releaseImport=resolve;});
    return {ok:true,json:async()=>({ok:true,data:url==='/api/updates/status'?{state:'BLOCKED'}:state})};
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/desktop_maintenance.js'),'utf8'),
    {document,fetch,setTimeout(fn){timers.push(fn);},window:{confirm:()=>confirms}});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();
  const dialog=all.find(e=>e.tag==='dialog');dialog.showModal();
  const file=all.find(e=>e.type==='file');assert.ok(dialog.children.includes(file));
  const choose=index=>all.filter(e=>e.tag==='button'&&e.textContent==='导入离线组件包').slice(-2)[index].events.click();
  choose(1);assert.equal(file.clicks,1);
  // The 15-second status tick must not disconnect or replace the open picker.
  await timers[0]();await flush();
  assert.equal(all.filter(e=>e.type==='file').length,1);assert.ok(dialog.children.includes(file));
  const archive={name:'browser.zip'};file.files=[archive];file.value='browser.zip';
  confirms=false;await file.events.change();assert.equal(file.value,'');
  assert.equal(requests.filter(r=>r.url.includes('/import?')).length,0);
  choose(0);file.events.cancel();file.files=[];await file.events.change();
  confirms=true;choose(1);file.files=[archive];
  const importing=file.events.change();await flush();
  const request=requests.find(r=>r.url.includes('/import?'));
  assert.equal(request.url,'/api/components/import?id=browser');assert.equal(request.options.body,archive);
  assert.equal(request.options.headers['Content-Type'],'application/zip');
  assert.ok(all.filter(e=>e.textContent==='导入离线组件包').slice(-2).every(e=>e.disabled));
  // Another change event while the original request is pending cannot repost.
  file.files=[archive];await file.events.change();
  assert.equal(requests.filter(r=>r.url.includes('/import?')).length,1);
  releaseImport();await importing;
  assert.ok(all.filter(e=>e.textContent==='导入离线组件包').slice(-2).every(e=>!e.disabled));
  choose(1);file.files=[archive];const retry=file.events.change();await flush();
  assert.equal(requests.filter(r=>r.url.includes('/import?')).length,2);
  releaseImport();await retry;
});
