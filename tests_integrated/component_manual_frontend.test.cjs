const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('manual download links are plain HTTPS and originals use the bounded raw endpoint',async()=>{
 const all=[],requests=[];
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.events={};this.style={};all.push(this);}
  append(...nodes){this.children.push(...nodes);}setAttribute(k,v){this[k]=v;}
  addEventListener(k,f){this.events[k]=f;}replaceChildren(){this.children=[];}
  click(){this.clicked=true;}showModal(){this.open=true;}
 }
 const page=new Element('page');page.classList={contains:()=>true};
 const document={readyState:'complete',hidden:false,createElement:t=>new Element(t),getElementById:id=>id==='page-settings'?page:null};
 const state={components:[{id:'browser',title:'Browser',version:'pinned',total:5,state:'failed',manual_files:[
  {name:'browser.zip',bytes:5,sha256:'a'.repeat(64),urls:['https://cdn.npmmirror.com/pinned.zip','https://storage.googleapis.com/pinned.zip','javascript:alert(1)']}
 ]}],update_policy:'locked'};
 const fetch=async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({ok:true,data:url==='/api/updates/status'?{state:'BLOCKED'}:state})};};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/desktop_maintenance.js'),'utf8'),{document,fetch,setTimeout(){},window:{confirm:()=>true}});
 await new Promise(r=>setImmediate(r));
 assert.equal(all.filter(e=>e.tag==='a').length,2);
 assert.ok(all.filter(e=>e.tag==='a').every(e=>e.target==='_blank'&&e.rel==='noopener noreferrer'));
 const raw=all.find(e=>e.textContent==='导入原始下载文件');assert.ok(raw);raw.events.click();
 const file=all.find(e=>e.type==='file');assert.equal(file.accept,'');
 const body={name:'browser.zip'};file.files=[body];await file.events.change();
 const sent=requests.find(r=>r.url==='/api/components/import-file?id=browser');assert.ok(sent);
 assert.equal(sent.options.headers['Content-Type'],'application/octet-stream');assert.equal(sent.options.body,body);
 assert.equal(file.value,'');
});
