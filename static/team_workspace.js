/* Shared origin, isolated backend: never copy drafts or attachment caches between accounts. */
(() => {
 const context=window.STUDIO_CONTEXT||{remote:false,workspace:'owner',admin:true};
 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
 const size=n=>{let x=Number(n)||0;for(const unit of ['B','KB','MB','GB','TB']){if(x<1024||unit==='TB')return x.toFixed(unit==='B'?0:1)+' '+unit;x/=1024}};
 const button=(text,fn,cls='secondary')=>{const e=node('button',text,cls);e.type='button';e.onclick=async()=>{e.disabled=true;try{await fn()}catch(err){toast(err.message,true)}finally{e.disabled=false}};return e};
 const nav=node('button','💾 文件与空间','nav');nav.dataset.page='team-storage';
 document.querySelector('.sidebar nav')?.insertBefore(nav,document.querySelector('.nav-divider'));
 const page=node('section',undefined,'page team-storage');page.id='page-team-storage';
 const heading=node('div',undefined,'team-heading');heading.append(node('h2','文件与空间'),node('p','素材和成片保存在主机。点击下载可保存到当前电脑；移入回收站7天后才会永久清理。'));
 const tools=node('div',undefined,'team-toolbar'),select=node('select');select.setAttribute('aria-label','查看用户空间');
 for(const space of context.spaces||[{id:'owner',user:'本机管理员'}]){const o=node('option',space.user);o.value=space.id;select.append(o)}
 select.value=context.workspace;tools.append(select);
 const summary=node('div',undefined,'team-summary'),filesBox=node('div'),trashBox=node('div'),opsBox=node('div'),taskBox=node('div');
 const filters=node('div',undefined,'team-toolbar'),category=node('select');
 for(const [value,label] of [['outputs','生成结果'],['assets','上传素材'],['product_knowledge','商品资料']]){const o=node('option',label);o.value=value;category.append(o)}
 let currentPage=0;filters.append(category,button('上一页',async()=>{currentPage=Math.max(0,currentPage-1);await loadFiles()}),button('下一页',async()=>{currentPage++;await loadFiles()}));
 const fileCount=node('span');filters.append(fileCount);
 page.append(heading,tools,summary,node('h3','主机上的文件'),filters,filesBox,node('h3','回收站'),trashBox,node('h3','提交与核对记录'),opsBox,node('h3','当前任务'),taskBox);
 document.querySelector('main')?.append(page);
 let prefix=()=>select.value===context.workspace?'':'/_remote/spaces/'+encodeURIComponent(select.value);
 const request=(path,opts)=>api(prefix()+path,opts);
 const write=(path,data)=>request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const link=(label,url)=>{const a=node('a',label,'team-download');a.href=prefix()+url;a.download='';return a};
 function row(title,detail,actions=[]){const r=node('div',undefined,'team-file-row'),info=node('div',undefined,'team-file-info');const t=node('span',title);t.title=title;info.append(t,node('small',detail));r.append(info,...actions);return r}
 async function loadFiles(){const list=await request('/api/storage/files?category='+category.value+'&page='+currentPage);filesBox.replaceChildren();fileCount.textContent=`共 ${list.total} 个文件 · 第 ${currentPage+1} 页`;
  for(const file of list.rows){const actions=[];if(file.url)actions.push(link('下载',file.url));actions.push(button('移入回收站',async()=>{if(!confirm('移入回收站：'+file.name+'\n相关历史记录暂时无法播放或下载，7天内可恢复。\n本操作不会验证你是否已下载完成。'))return;await write('/api/storage/trash',{path:file.path,confirmed:true});await refresh()}));filesBox.append(row(file.name,size(file.bytes)+' · '+new Date(file.modified*1000).toLocaleString(),actions))}
  if(!list.rows.length)filesBox.append(node('p','这里暂时没有文件。','team-empty'));
 }
 async function refresh(){summary.textContent='正在读取空间信息…';const s=await request('/api/storage/status');summary.replaceChildren();
  const used=Object.values(s.usage).reduce((a,b)=>a+b,0);summary.append(node('b',`当前空间 ${size(used)} / ${size(s.quota_bytes)}`),node('span',`主机剩余 ${size(s.disk_free)} · 安全预留 ${size(s.reserve_bytes)}`));
  const bar=node('progress');bar.max=s.quota_bytes;bar.value=used;bar.setAttribute('aria-label','当前空间占用');summary.append(bar,node('small',s.retention+' 并发保护：每个空间最多2个任务，主机最多4个；重任务可能占用多个执行名额。'));
  trashBox.replaceChildren();for(const item of s.trash){const controls=[];
   if(['ready','moving','restoring'].includes(item.state))controls.push(button('恢复',async()=>{await write('/api/storage/restore',{id:item.id});await refresh()}));
   if(item.state==='ready')controls.push(button('永久删除',async()=>{if(!confirm('永久删除后无法恢复：'+item.original+'\n确认已保存需要的文件？'))return;await write('/api/storage/purge',{id:item.id,confirmed:true});await refresh()},'team-danger'));
   trashBox.append(row(item.original,size(item.size)+' · '+(item.state==='ready'?'到期 '+new Date((item.created+7*86400)*1000).toLocaleString():'需要检查：'+item.state),controls));}
  if(!s.trash.length)trashBox.append(node('p','回收站为空。','team-empty'));
  opsBox.replaceChildren();for(const op of s.operations){const labels={pending:'处理中',done:'已有返回记录',unknown:'结果待核对',acknowledged:'已人工核对'};const controls=[];
   if(op.state==='unknown')controls.push(button('记录核对结果',async()=>{const note=prompt('请先在供应商后台核对任务和账单，再填写核对结果。此操作不重新提交任务。');if(!note)return;await write('/api/safety/acknowledge',{id:op.id,confirmed:true,note});await refresh()}));
   opsBox.append(row(op.route,(labels[op.state]||op.state)+' · '+op.id,controls));}
  if(!s.operations.length)opsBox.append(node('p','尚无提交记录。','team-empty'));
  taskBox.replaceChildren();
  for(const task of s.tasks||[])taskBox.append(row(task.id,(task.kind||'任务')+' · '+(task.status||'处理中')));
  await loadFiles();
 }
 tools.append(button('刷新',refresh),button('清理过期缓存',async()=>{const r=await write('/api/storage/clean',{});toast('已清理 '+r.cleaned_files+' 项；上传素材和成片不自动删除。');await refresh()}));
 select.onchange=()=>{currentPage=0;refresh().catch(e=>toast(e.message,true))};category.onchange=()=>{currentPage=0;loadFiles().catch(e=>toast(e.message,true))};
 // Existing navigation binds only the original buttons. Keep storage navigation explicit.
 nav.onclick=()=>{document.querySelectorAll('.page.active,.nav.active').forEach(e=>e.classList.remove('active'));nav.classList.add('active');page.classList.add('active');document.getElementById('pageTitle').textContent='文件与空间';refresh().catch(e=>{summary.textContent=e.message;toast(e.message,true)})};
 document.querySelectorAll('.nav:not([data-page="team-storage"])').forEach(e=>e.addEventListener('click',()=>page.classList.remove('active')));
 if(context.remote){
   document.body.classList.add('team-remote');
   const settings=document.getElementById('page-settings');
   if(settings){
    const note=node('p','远程设置为只读。修改密钥、全局参数、备份和安装依赖，请在主机本地入口操作。','team-settings-note');settings.prepend(note);
    settings.querySelectorAll('input,select,textarea').forEach(e=>e.disabled=true);
    settings.querySelectorAll('button').forEach(e=>{if(!['runSystemHealth','repairSystem'].includes(e.id))e.setAttribute('data-host-only','')});
   }
   const badge=node('span',context.user,'team-user-badge');badge.title='当前账号的独立任务和素材空间';document.querySelector('.top-actions')?.prepend(badge);
   const output=document.getElementById('openOutput');if(output){output.textContent='文件与下载';output.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();nav.click()},true)}
   window.openOutputFolder=()=>nav.click();
   // Host configuration controls should not invite operations that the gateway refuses.
   for(const id of ['modelSettingsShortcut','setOpenLuxApiKey','saveOpenLuxApiKey','createBackup','createBackupWithOutputs'])document.getElementById(id)?.setAttribute('hidden','');
   document.querySelectorAll('[data-open-task-output]').forEach(e=>e.hidden=true);
 }
})();
