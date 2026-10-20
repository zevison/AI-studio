// Pure JavaScript unit tests, with fake DOM/I/O; these are not browser acceptance.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/assistant_bright.js'),'utf8');
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};
function harness(route,blockedStorage=false){
  const elements=new Map();
  function node(){return {value:'',children:[],hidden:false,disabled:false,textContent:'',scrollHeight:0,
    classList:{toggle(){},add(){},remove(){}},append(...items){this.children.push(...items)},
    replaceChildren(){this.children=[]},querySelector(){return null},querySelectorAll(){return []},
    addEventListener(){},setAttribute(){},after(){},focus(){}};}
  const get=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id)};
  const document={readyState:'loading',getElementById:get,addEventListener(){},createElement:node,createTextNode:text=>({textContent:text})};
  const localStorage={getItem(){if(blockedStorage)throw Error('denied');return null},
    setItem(){if(blockedStorage)throw Error('quota')},removeItem(){if(blockedStorage)throw Error('denied')}};
  const readers=[];
  class FileReader{readAsDataURL(){readers.push(this)}}
  const context=vm.createContext({document,localStorage,FileReader,window:{},console,TextDecoder,
    fetch:async(url,options)=>({ok:true,json:async()=>({ok:true,data:await route(url,options)})}),
    setInterval,clearInterval,crypto:{randomUUID:()=> 'test-request-1234'}});
  const exposed="window.GPTChat={load,openChat,newChat,send,addFiles,cache,"+
    "pasteAttachments,transferredFiles,state:()=>({cid,files,loadingChat}),seed:(id,items=[])=>{cid=id;files=items},"+
    "replaceSender:fn=>{sendOnce=fn}};";
  vm.runInContext(source.replace('window.GPTChat={load};',exposed),context);
  return {chat:context.window.GPTChat,get,readers};
}
test('rapid first sends admit only one asynchronous preparation',async()=>{
  const h=harness(()=>[]),gate=deferred();let attempts=0;
  h.chat.replaceSender(async()=>{attempts++;await gate.promise});
  const first=h.chat.send();await h.chat.send();assert.equal(attempts,1);
  gate.resolve();await first;await h.chat.send();assert.equal(attempts,2);
});
test('rapid new-chat clicks create only one conversation',async()=>{
  const gate=deferred();let creates=0;
  const h=harness(async url=>{if(url.endsWith('/new')){creates++;await gate.promise;return {id:'fresh'}}
    return url.includes('call-state')?null:[]});
  const first=h.chat.newChat();await h.chat.newChat();assert.equal(creates,1);
  gate.resolve();await first;assert.equal(h.chat.state().cid,'fresh');
});
test('late history responses cannot replace the newly selected conversation',async()=>{
  const gate=deferred();
  const h=harness(async url=>{if(url.includes('conversation_id=old'))await gate.promise;
    return url.includes('call-state')?null:[]});
  h.chat.seed('initial',[{name:'private.txt',data:'secret'}]);
  const old=h.chat.openChat('old');await h.chat.openChat('new');gate.resolve();await old;
  assert.equal(h.chat.state().cid,'new');assert.equal(h.chat.state().files.length,0);
  assert.equal(h.chat.state().loadingChat,false);assert.equal(h.get('aiSend').disabled,false);
});
test('file read finishing after a chat switch cannot attach to the new chat',async()=>{
  const h=harness(url=>url.includes('call-state')?null:[]);h.chat.seed('first');
  const adding=h.chat.addFiles([{name:'private.txt',size:6}]);
  const rejected=assert.rejects(adding,/聊天已切换/);
  await h.chat.openChat('second');
  h.readers[0].result='data:text/plain;base64,c2VjcmV0';h.readers[0].onload();await rejected;
  assert.equal(h.chat.state().files.length,0);
});
test('blocked localStorage cannot break SQLite history or leave send disabled',async()=>{
  const h=harness(url=>url.includes('call-state')?null:[],true);
  await h.chat.openChat('stored');assert.equal(h.chat.state().cid,'stored');
  assert.equal(h.get('aiSend').disabled,false);assert.equal(h.chat.cache.get('missing'),null);
  h.chat.cache.set('draft','text');h.chat.cache.remove('draft');
});
test('failed history load retains the active chat and releases the send lock',async()=>{
  const h=harness(()=>{throw Error('disk unavailable')});h.chat.seed('active');
  await assert.rejects(h.chat.openChat('unreadable'),/disk unavailable/);
  assert.equal(h.chat.state().cid,'active');assert.equal(h.chat.state().loadingChat,false);
  assert.equal(h.get('aiSend').disabled,false);
});
test('failed conversation creation can be retried without a stuck preparation lock',async()=>{
  let attempts=0;const h=harness(()=>{attempts++;throw Error('disk unavailable')});
  await assert.rejects(h.chat.newChat(),/disk unavailable/);
  await assert.rejects(h.chat.newChat(),/disk unavailable/);assert.equal(attempts,2);
});
test('pasting a copied TXT file attaches its bytes, independent of image MIME types',async()=>{
  const h=harness(()=>[]);let prevented=false;
  const file={name:'notes.txt',size:5,type:'text/plain'};
  const pasting=h.chat.pasteAttachments({clipboardData:{files:[],items:[{kind:'file',type:'text/plain',getAsFile:()=>file}]},preventDefault(){prevented=true}});
  assert.equal(prevented,true);assert.equal(h.readers.length,1);
  h.readers[0].result='data:text/plain;base64,aGVsbG8=';h.readers[0].onload();await pasting;
  assert.equal(h.chat.state().files[0].name,'notes.txt');assert.equal(h.chat.state().files[0].data,'aGVsbG8=');
});
test('ordinary text paste retains native insertion and creates no attachment',async()=>{
  const h=harness(()=>[]);let prevented=false;
  await h.chat.pasteAttachments({clipboardData:{types:['text/plain'],items:[{kind:'string',type:'text/plain'}]},preventDefault(){prevented=true}});
  assert.equal(prevented,false);assert.equal(h.chat.state().files.length,0);
});
test('FileList takes precedence over duplicate transfer items',()=>{
  const h=harness(()=>[]),file={name:'notes.txt'};
  const result=h.chat.transferredFiles({files:[file],items:[{kind:'file',getAsFile:()=>file}]});
  assert.equal(result.length,1);assert.equal(result[0],file);
});
test('unavailable clipboard file contents produce an actionable message',async()=>{
  const h=harness(()=>[]);let prevented=false;
  await h.chat.pasteAttachments({clipboardData:{types:['Files'],items:[{kind:'file',getAsFile:()=>null}]},preventDefault(){prevented=true}});
  assert.equal(prevented,true);assert.match(h.get('aiStatus').textContent,/没有提供可读取的文件/);
});

function finishReaders(h){for(const reader of h.readers){reader.result='data:image/png;base64,aGVsbG8=';reader.onload();}}
test('twenty images render, including adding past the old three-file limit',async()=>{
 const h=harness(()=>[]);h.chat.seed('active');
 const first=h.chat.addFiles(Array.from({length:3},(_,i)=>({name:`${i}.png`,size:5})));finishReaders(h);await first;
 const rest=h.chat.addFiles(Array.from({length:17},(_,i)=>({name:`${i+3}.png`,size:5})));finishReaders(h);await rest;
 assert.equal(h.chat.state().files.length,20);assert.equal(h.get('aiFileList').children.length,20);assert.match(h.get('aiStatus').textContent,/20\/20/);
 await assert.rejects(h.chat.addFiles([{name:'extra.png',size:5}]),/最多20/);assert.equal(h.chat.state().files.length,20);
});
test('parallel additions cannot bypass the twenty-file limit or discard accepted files',async()=>{
 const h=harness(()=>[]);h.chat.seed('active');
 const first=h.chat.addFiles(Array.from({length:12},()=>({name:'a.png',size:5})));
 const second=h.chat.addFiles(Array.from({length:12},()=>({name:'b.png',size:5})));
 const rejected=assert.rejects(second,/最多20/);finishReaders(h);await first;await rejected;
 assert.equal(h.chat.state().files.length,12);
});
test('oversized batch fails before reading image data',async()=>{
 const h=harness(()=>[]);await assert.rejects(h.chat.addFiles(Array.from({length:4},()=>({name:'large.png',size:3*1024*1024}))),/总大小/);
 assert.equal(h.readers.length,0);assert.equal(h.chat.state().files.length,0);
});
