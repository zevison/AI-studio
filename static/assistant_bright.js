/* Seedance Studio V4.1 high-capability GPT chat. No product / video task access. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
const MAX_CHAT_ATTACHMENTS=20;
async function api(url,payload){const opts=payload===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),cache:'no-store'};const res=await fetch(url,opts);const j=await res.json();if(!res.ok||!j.ok)throw Error(j.error||'接口返回失败');return j.data}
let cid='',busy=false,preparing=false,creating=false,loadingChat=false,chatLoadVersion=0,attachmentVersion=0,loaded=false,files=[],lastCall=null,displayRows=[];
// Local storage is optional; blocked storage must never prevent a chat request
// from releasing its UI lock or prevent SQLite history from being displayed.
const cache={get(key){try{return localStorage.getItem('workspace:'+(window.STUDIO_CONTEXT?.workspace||'owner')+':'+key)}catch{return null}},set(key,value){try{localStorage.setItem('workspace:'+(window.STUDIO_CONTEXT?.workspace||'owner')+':'+key,value)}catch{}},remove(key){try{localStorage.removeItem('workspace:'+(window.STUDIO_CONTEXT?.workspace||'owner')+':'+key)}catch{}}};
const notify=msg=>{if($('aiStatus'))$('aiStatus').textContent=msg};
const selectedModel=()=>$('aiModel').value==='custom'?$('aiCustomModel').value.trim():$('aiModel').value;
const screen=document.getElementById('aiMessages');
// Display real message times in the viewer's local timezone. The stored UTC/Unix
// time is authoritative on reload; unsent UI state is not treated as persisted.
function normalizeEpoch(raw){
  const n=Number(raw);return Number.isFinite(n) && n>0 ? (n>1e12?n:n*1000):null;
}
function formatTime(raw){
  const ms=normalizeEpoch(raw);if(!ms)return '时间未知';
  const date=new Date(ms),now=new Date();
  const pad=n=>String(n).padStart(2,'0');
  const clock=[date.getHours(),date.getMinutes(),date.getSeconds()].map(pad).join(':');
  if(date.getFullYear()===now.getFullYear()&&date.getMonth()===now.getMonth()&&date.getDate()===now.getDate())return clock;
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${clock}`;
}
function durationText(seconds){
  const v=Math.max(0,Number(seconds)||0);
  return v<60?`${v.toFixed(1)}秒`:`${Math.floor(v/60)}分${(v%60).toFixed(1)}秒`;
}
function metaFor(text){const elem=document.createElement('div');elem.className='gpt-message-time';elem.textContent=text;return elem;}
function setMessageMeta(node,role,when,elapsed=null,extra=''){
  const elem=node.querySelector('.gpt-message-time');if(!elem)return;
  const prefix=role==='user'?'发送于 ':'回复于 ';
  let text=(extra?extra+' · ':'')+prefix+formatTime(when);
  if(elapsed!==null&&Number.isFinite(Number(elapsed)))text+=' · 耗时 '+durationText(elapsed);
  elem.textContent=text;
  const ms=normalizeEpoch(when);
  if(ms)elem.title=new Date(ms).toLocaleString('zh-CN',{hour12:false});
}
function bubble(role,content,createdAt=null,elapsed=null){
  const node=document.createElement('article');node.className='gpt-bubble '+(role==='user'?'user':'assistant');
  const title=document.createElement('strong');title.textContent=role==='user'?'你':'GPT';
  const text=document.createElement('div');text.className='gpt-answer';text.textContent=content;
  const meta=metaFor('');node.append(title,text,meta);screen.append(node);
  setMessageMeta(node,role,createdAt??Date.now(),elapsed);
  screen.scrollTop=screen.scrollHeight;return text;
}
async function loadList(){const rows=await api('/api/assistant/conversations');const list=$('aiConversationList');list.replaceChildren();rows.forEach(r=>{const b=document.createElement('button');b.type='button';b.className='gpt-history'+(r.id===cid?' active':'');b.dataset.chatId=r.id;b.textContent=r.title;b.addEventListener('click',()=>openChat(r.id).catch(e=>notify(e.message)));list.append(b)});return rows;}
async function openChat(id,before=null){if(busy)return;const version=++chatLoadVersion;loadingChat=true;$('aiSend').disabled=true;try{const oldHeight=screen.scrollHeight;const [chunk,call]=await Promise.all([api('/api/assistant/messages?conversation_id='+encodeURIComponent(id)+(before?'&before_id='+encodeURIComponent(before):'')),api('/api/assistant/call-state?conversation_id='+encodeURIComponent(id))]);if(version!==chatLoadVersion)return;if(cid!==id){attachmentVersion++;if(cid){files=[];showAttachments();}}cid=id;const rows=before?[...chunk,...displayRows]:chunk;displayRows=rows;screen.replaceChildren();if(chunk.length===200){const older=document.createElement('button');older.type='button';older.textContent='加载更早的本地消息';older.className='secondary';older.onclick=()=>openChat(cid,rows[0].id).catch(e=>notify(e.message));screen.append(older)}if(!rows.length)screen.innerHTML='<div class="gpt-empty"><h3>有什么可以帮你？</h3><p>高性能 GPT 对话，用户可以自行启用联网或上传文件。</p></div>';let lastUserSentAt=null;
for(const item of rows){
 const isAssistant=item.role==='assistant';
 const sec=Number(item.created_at)||null;
 const elapsed=isAssistant && sec && lastUserSentAt?Math.max(0,sec-lastUserSentAt):null;
 const answer=bubble(item.role,item.content,item.created_at,elapsed);
 if(item.attachments?.length){const list=document.createElement('div');list.className='gpt-saved-attachments';for(const itemFile of item.attachments){const tag=document.createElement('details');const heading=document.createElement('summary');heading.textContent='附件：'+itemFile.name;tag.append(heading);let fetched=false;tag.addEventListener('toggle',async()=>{if(!tag.open||fetched)return;fetched=true;try{const f=await api(itemFile.local_url);if(f.kind==='image'&&/^data:image\/(png|jpeg|webp);base64,/.test(f.value)){const img=document.createElement('img');img.src=f.value;img.alt=f.name;tag.append(img)}else{const pre=document.createElement('pre');pre.textContent=f.value;tag.append(pre)}}catch(e){fetched=false;notify('本地附件读取失败：'+e.message)}});list.append(tag)}answer.after(list)}
 if(item.role==='user')lastUserSentAt=sec;
 else if(isAssistant)lastUserSentAt=null;
}$('aiConversationList').querySelectorAll('[data-chat-id]').forEach(b=>b.classList.toggle('active',b.dataset.chatId===cid));screen.scrollTop=before?screen.scrollHeight-oldHeight:screen.scrollHeight;cache.set('seedance-active-chat',cid);$('aiPrompt').value=cache.get('seedance-chat-draft:'+cid)||'';lastCall=call;$('aiContinue').hidden=!lastCall||!['submission_unknown','partial'].includes(lastCall.status);if(!$('aiContinue').hidden)notify('旧请求可能已扣费。先刷新记录；等待结束后可保留旧请求，继续新的问题。');}finally{if(version===chatLoadVersion){loadingChat=false;$('aiSend').disabled=busy;}}}
async function newChat(){if(busy||creating||loadingChat)return;creating=true;try{const r=await api('/api/assistant/new',{});await loadList();await openChat(r.id);}finally{creating=false;}}
function showAttachments(){const wrap=$('aiFileList');if(!wrap)return;wrap.replaceChildren();for(const [index,file] of files.entries()){const tag=document.createElement('span');tag.className='gpt-attach';if(/\.(png|jpe?g|webp)$/i.test(file.name)){const img=document.createElement('img');img.src='data:image/'+(/\.png$/i.test(file.name)?'png':/\.webp$/i.test(file.name)?'webp':'jpeg')+';base64,'+file.data;img.alt=file.name;tag.append(img)}tag.append(document.createTextNode(file.name+' '));const b=document.createElement('button');b.type='button';b.textContent='删除';b.setAttribute('aria-label','删除附件 '+file.name);b.onclick=()=>{attachmentVersion++;files.splice(index,1);showAttachments()};tag.append(b);wrap.append(tag);}}
async function addFiles(selected){if(loadingChat)throw Error('正在切换聊天，请稍后添加附件');const version=attachmentVersion;if(files.length+selected.length>MAX_CHAT_ATTACHMENTS)throw Error('每条消息最多'+MAX_CHAT_ATTACHMENTS+'个附件');if(selected.reduce((n,f)=>n+f.size,0)>10*1024*1024)throw Error('附件总大小不得超过10MB');const encoded=await Promise.all(selected.map(encodeFile));if(version!==attachmentVersion)throw Error('聊天已切换，请在当前聊天重新选择附件');const combined=[...files,...encoded];if(combined.length>MAX_CHAT_ATTACHMENTS)throw Error('每条消息最多'+MAX_CHAT_ATTACHMENTS+'个附件');if(combined.reduce((n,f)=>n+f.data.length,0)>Math.ceil(10*1024*1024*4/3))throw Error('附件总大小不得超过10MB');files=combined;showAttachments();notify('已附加 '+files.length+'/'+MAX_CHAT_ATTACHMENTS+' 个文件（合计上限10MB）。发送后随聊天记录保存到本机。');}
async function encodeFile(file){if(file.size>6*1024*1024)throw Error('单文件不得超过6MB');if(/\.(png|jpe?g|webp)$/i.test(file.name)&&file.size>4*1024*1024)throw Error('图片不得超过4MB');const ext=(file.name.split('.').pop()||'').toLowerCase();if(!['png','jpg','jpeg','webp','pdf','docx','xlsx','txt','md','csv','json'].includes(ext))throw Error('不支持的文件类型：'+ext);return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(Error('文件读取失败'));reader.onload=()=>resolve({name:file.name,data:String(reader.result).split(',')[1]||''});reader.readAsDataURL(file)});}
function transferredFiles(transfer){const listed=Array.from(transfer?.files||[]);if(listed.length)return listed;return Array.from(transfer?.items||[]).filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);}
async function pasteAttachments(e){const pasted=transferredFiles(e.clipboardData);if(pasted.length){e.preventDefault();try{await addFiles(pasted)}catch(ex){notify('粘贴附件失败：'+ex.message)}return;}if(Array.from(e.clipboardData?.types||[]).includes('Files')){e.preventDefault();notify('浏览器没有提供可读取的文件内容，请点击“＋ 图片 / 文档”选择文件。');}}
function parseSSE(value){let event='message',payload='';for(const line of value.split('\n')){if(line.startsWith('event:'))event=line.slice(6).trim();if(line.startsWith('data:'))payload+=line.slice(5).trimStart()}if(!payload)return null;try{return {name:event,data:JSON.parse(payload)}}catch{return null}}
async function send(){if(busy||preparing||creating||loadingChat)return;preparing=true;try{await sendOnce()}catch(e){notify('发送准备失败：'+e.message)}finally{preparing=false;}}
async function sendOnce(){const input=$('aiPrompt');let message=input.value.trim();if(!message&&files.length)message='请分析我附加的图片或文件。';if(!message)return;if(!cid)await newChat();if(!cid)return;
const model=selectedModel();if(!model){notify('模型编号不能为空');return;}
busy=true;$('aiSend').disabled=true;const attachments=files.slice();const requestId=crypto.randomUUID().replaceAll('-','');files=[];showAttachments();input.value='';cache.remove('seedance-chat-draft:'+cid);if(screen.querySelector('.gpt-empty'))screen.replaceChildren();
const sentLocal=Date.now();const userAnswer=bubble('user',message,sentLocal);
const reply=bubble('assistant','',sentLocal);const replyNode=reply.closest('.gpt-bubble');
let serverSentAt=sentLocal;let started=false,done=false,gotText=false;
const updatePending=()=>{if(done)return;const secs=(Date.now()-sentLocal)/1000;
 replyNode.querySelector('.gpt-message-time').textContent=(gotText?'回复生成中':'等待 GPT 回复')+' · 已等待 '+durationText(secs);};
updatePending();const ticker=setInterval(updatePending,1000);
notify('正在调用 '+model+'…等待首段文字。模型生成费用仅按一次请求计算，不自动重试。');
try{const response=await fetch('/api/gpt-v41/chat/stream',{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},body:JSON.stringify({conversation_id:cid,request_id:requestId,message,model,approve_model_cost:true,thinking:$('aiThinking').value,mode:$('aiWebSearch').checked?'web':'chat',attachments})});
if(!response.ok){const body=await response.text();throw Error('本地接口 '+response.status+': '+body.slice(0,180))}
if(!response.body)throw Error('此浏览器不支持流式响应，请更新浏览器');const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
while(true){const {value,done:end}=await reader.read();if(end)break;buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');let pos;while((pos=buffer.indexOf('\n\n'))!==-1){const packet=buffer.slice(0,pos);buffer=buffer.slice(pos+2);const ev=parseSSE(packet);if(!ev)continue;
if(ev.name==='begin'){
 started=true;
 if(normalizeEpoch(ev.data.sent_at)){
  serverSentAt=normalizeEpoch(ev.data.sent_at);
  setMessageMeta(userAnswer.closest('.gpt-bubble'),'user',serverSentAt);
 }
 notify('已提交一次模型请求：'+ev.data.model+'；正在等待模型输出…');
 }
if(ev.name==='status')notify(ev.data.text);
if(ev.name==='delta'){gotText=true;reply.textContent+=String(ev.data.text||'');screen.scrollTop=screen.scrollHeight;notify('正在逐步生成回复…')}
if(ev.name==='error'){const error=Error(ev.data.message||'模型响应失败');error.state=ev.data.state;throw error}
if(ev.name==='done'){
 done=true;const repliedAt=normalizeEpoch(ev.data.reply_at)||Date.now();
 const measured=Number(ev.data.elapsed_seconds);
 const elapsed=Number.isFinite(measured)&&measured>=0?measured:(repliedAt-serverSentAt)/1000;
 setMessageMeta(replyNode,'assistant',repliedAt,elapsed,ev.data.complete?'':'部分回复');
 notify(ev.data.complete?'回复完成 · '+(ev.data.actual_model?'返回模型：'+ev.data.actual_model:'请求模型：'+ev.data.model+'（供应商未返回模型标识）')+' · 耗时 '+durationText(elapsed)+' · 最终费用以 OpenLux 账单为准':'只收到部分回复，完整性未知；不要立即重试付费请求');
 }
}}
if(!done)throw Error('连接中断或供应商未正常结束回复。请求结果不确定，禁止自动重试');
if(!gotText)reply.textContent='（模型没有返回可显示文字）';
await loadList();
}catch(e){
 reply.textContent+=(reply.textContent?'\n\n':'')+'[请求未完整完成] '+e.message;
 const meta=replyNode.querySelector('.gpt-message-time');
 meta.textContent='未完整回复 · 等待 '+durationText((Date.now()-sentLocal)/1000)+' · '+formatTime(Date.now());
 if(e.state==='not_submitted'||e.state==='rejected'){input.value=message;files=attachments;showAttachments();notify('本次没有完整回复：'+e.message+'。内容已恢复，请修正后自行发送。')}
 else{input.value=message;files=attachments;showAttachments();try{cache.set('seedance-chat-draft:'+cid,message)}catch{}notify('连接已中断；草稿和附件已恢复。先刷新记录确认原请求，避免再次发送相同问题而重复扣费。');}
}finally{clearInterval(ticker);busy=false;$('aiSend').disabled=false;try{lastCall=await api('/api/assistant/call-state?conversation_id='+encodeURIComponent(cid));$('aiContinue').hidden=!lastCall||!['submission_unknown','partial'].includes(lastCall.status)}catch{}input.focus();}}
async function diagnose(){const el=$('aiDiagnostics');el.textContent='只读接口检测中…';try{const r=await api('/api/assistant/diagnostics?model='+encodeURIComponent(selectedModel()));el.textContent=r.status==='reachable'?'网关已连接；账户模型列表中：'+(r.model_listed===true?'有此模型':r.model_listed===false?'未列出':'待验证'):'接口异常：'+(r.message||r.status)}catch(e){el.textContent='接口诊断失败：'+e.message}}
async function syncModels(){const btn=$('aiSyncModels');btn.disabled=true;const current=selectedModel();$('aiDiagnostics').textContent='正在只读读取账户支持的聊天模型…';try{const r=await api('/api/gpt-v41/models?force=1');const select=$('aiModel');const names=new Set(r.models||[]);select.replaceChildren();const presets=r.presets||[];for(const p of presets){const opt=document.createElement('option');opt.value=p.id;opt.textContent=p.label+(r.verified&&!names.has(p.id)?'（账户未列出）':'');select.add(opt)}for(const m of r.models||[]){if(presets.some(p=>p.id===m))continue;const opt=document.createElement('option');opt.value=m;opt.textContent=m+'（账户可见）';select.add(opt)}const custom=document.createElement('option');custom.value='custom';custom.textContent='自定义模型编号';select.add(custom);if([...select.options].some(x=>x.value===current))select.value=current;else{select.value='custom';$('aiCustomModel').value=current}
$('aiCustomModel').hidden=select.value!=='custom';$('aiDiagnostics').textContent=r.verified?'已从当前账户读取 '+r.models.length+' 个候选聊天模型；列表可见不等于生成接口已验证':(r.error||'尚未验证账户模型权限');}
catch(e){$('aiDiagnostics').textContent='模型列表同步失败：'+e.message}
finally{btn.disabled=false;}}
async function load(){if(loaded)return;loaded=true;try{const rows=await loadList();const saved=cache.get('seedance-active-chat');if(rows.length)await openChat(rows.find(r=>r.id===saved)?.id||rows[0].id);const prev=cache.get('seedance-gpt-model');if(prev&&[...$('aiModel').options].some(x=>x.value===prev))$('aiModel').value=prev;$('aiCustomModel').hidden=$('aiModel').value!=='custom'}catch(e){loaded=false;notify('读取聊天记录失败：'+e.message)}}
function init(){if(!$('aiSend'))return;
notify('每条消息最多20个图片或文档附件；单张图片不超过4MB，附件合计不超过10MB。');
$('aiNew').onclick=()=>newChat().catch(e=>notify(e.message));$('aiSend').onclick=send;$('aiDiagnose').onclick=diagnose;$('aiSyncModels').onclick=syncModels;$('aiModel').onchange=()=>{$('aiCustomModel').hidden=$('aiModel').value!=='custom';cache.set('seedance-gpt-model',$('aiModel').value)};
$('aiGotoSettings').onclick=()=>document.querySelector('.nav[data-page="settings"]')?.click();$('aiPrompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send()}});
$('aiFiles').onchange=async e=>{try{await addFiles([...e.target.files])}catch(ex){notify('附件失败：'+ex.message)}finally{e.target.value=''}};
$('aiPrompt').addEventListener('input',()=>{if(cid)try{cache.set('seedance-chat-draft:'+cid,$('aiPrompt').value)}catch{}});
$('aiPrompt').addEventListener('paste',pasteAttachments);
const composer=document.querySelector('.gpt-composer');composer.addEventListener('dragover',e=>{if(Array.from(e.dataTransfer?.types||[]).includes('Files')){e.preventDefault();composer.classList.add('drag-active')}});composer.addEventListener('dragleave',()=>composer.classList.remove('drag-active'));composer.addEventListener('drop',async e=>{e.preventDefault();composer.classList.remove('drag-active');const dropped=transferredFiles(e.dataTransfer);if(!dropped.length){notify('拖入内容中没有可读取的文件，请点击“＋ 图片 / 文档”选择文件。');return;}try{await addFiles(dropped)}catch(ex){notify('拖入附件失败：'+ex.message)}});
$('aiRefresh').onclick=()=>{if(!busy&&cid)openChat(cid).catch(e=>notify(e.message))};
$('aiContinue').onclick=async()=>{if(!lastCall)return;if(!confirm('旧请求可能已经扣费，此操作只保留旧记录并允许继续新问题，不会重新发送旧问题。是否继续？'))return;try{await api('/api/assistant/acknowledge',{conversation_id:cid,call_id:lastCall.id,acknowledge_possible_charge:true});await openChat(cid);notify('旧请求已保留，可以继续新的问题。实际扣费仍以账单为准。')}catch(e){notify(e.message)}};
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();window.GPTChat={load};
})();
