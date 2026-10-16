/* V4.2.3 unified history view. Data is read-only; this never submits provider jobs. */
(function(){
'use strict';
const byId=id=>document.getElementById(id);
const escapeHtml=x=>String(x??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const label={video:'视频生成',image:'图片生成',video_image:'生成任务',gpt:'GPT 对话',product:'商品理解',script:'创意脚本',brightdata:'TikTok 采集'};
let cache=null,filter='all',search='';
async function get(path){const r=await fetch(path,{cache:'no-store'});const obj=await r.json();if(!r.ok||obj.ok!==true)throw Error(obj.error||'读取历史数据失败');return obj.data;}
function money(v,type){return typeof v==='number'&&Number.isFinite(v)?'$'+v.toFixed(4):type==='actual'?'没有已核实的实际费用':'价格未知／无可靠估算';}
function stamp(s){const t=Number(s);return t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'时间未记录';}
function safeText(x){return escapeHtml(x).slice(0,520);}
function topLine(data){
 const c=data.summary||{},sources=data.sources||{},summary=byId('costSummary');if(!summary)return;
 const chips=[['video_image','视频/图片'],['gpt','GPT 对话'],['product','商品理解'],['script','创意脚本'],['brightdata','手动采集']]
   .map(([k,n])=>`<span class="hc-chip">${n} ${sources[k]?.rows||0}</span>`).join('');
 summary.innerHTML=`<div class="hc-stat-grid"><div class="hc-stat"><b>${c.total_records||0}</b><small>已保存的调用和任务</small></div><div class="hc-stat"><b>${money(c.estimated_usd)}</b><small>有记录的费用预估合计</small></div><div class="hc-stat"><b>${money(c.actual_usd,'actual')}</b><small>已存储的实际费用（可能不完整）</small></div></div>
 <div class="hc-chips">${chips}</div><div class="hc-note">费用仅汇总本地有依据的记录，绝不把未知金额当作0元或免费。实时账户余额不等于历史实际扣费。</div>
 <div class="hc-foot"><span>当前数据源：data/studio.db</span><button type="button" class="secondary" id="hcRefresh">只读刷新</button></div>`;
 byId('hcRefresh').onclick=()=>refreshUnifiedHistory();
}
function storedPrompt(task){
 const req=task.request||{};
 const original=String(req.original_instruction||req.user_prompt||req.user_instruction||req.instruction||'').trim();
 if(original)return {text:typeof window.normalizePromptMentions==='function'?window.normalizePromptMentions(original):original,label:'保存的原始提示词'};
 const fallback=String(req.prompt||'').trim();
 if(fallback)return {text:fallback,label:'旧任务原始输入未保存；展示模型提交提示词'};
 return {text:'',label:'旧任务没有可恢复的提示词'};
}
async function showHistoryVideoPrompt(id,container){
 const existing=container.querySelector('.hc-prompt-panel');if(existing){existing.remove();return}
 const panel=document.createElement('div');panel.className='hc-prompt-panel';panel.textContent='正在读取本地历史任务…';container.appendChild(panel);
 try{
  const task=await get('/api/tasks/'+encodeURIComponent(id));
  if(!panel.isConnected)return;
  const p=storedPrompt(task);panel.replaceChildren();
  const label=document.createElement('div');label.className='hc-prompt-meta';label.textContent=p.label+' · '+task.id;
  const pre=document.createElement('pre');pre.className='hc-prompt-full';pre.textContent=p.text||'该历史任务未保存文字提示词';
  const actions=document.createElement('div');actions.className='hc-prompt-actions';
  const copy=document.createElement('button');copy.type='button';copy.className='secondary';copy.textContent='复制完整提示词';copy.disabled=!p.text;
  copy.onclick=async()=>{try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(p.text);else{const field=document.createElement('textarea');field.value=p.text;document.body.appendChild(field);field.select();if(!document.execCommand('copy'))throw Error('复制不可用');field.remove()}copy.textContent='已复制'}catch(e){copy.textContent='请选中文本复制'}};
  const reuse=document.createElement('button');reuse.type='button';reuse.className='secondary';reuse.textContent='复用素材与参数';reuse.onclick=()=>{if(typeof window.reuseSeedanceTask==='function')window.reuseSeedanceTask(id)};
  actions.append(copy,reuse);panel.append(label,pre,actions);
 }catch(e){panel.textContent='读取历史提示词失败：'+e.message}
}
function historyRows(data){
 const el=byId('taskHistory');if(!el)return;
 const entries=data.entries||[];
 let list=entries.filter(x=>(filter==='all'||x.kind===filter)&&(!search||[x.label,x.model,x.id,x.status].some(v=>String(v||'').toLowerCase().includes(search))));
 const filters=[['all','全部'],['video','视频'],['image','图片'],['gpt','GPT'],['product','商品'],['script','创意脚本'],['brightdata','采集']];
 const toolbar=`<div class="hc-tools"><label>类型 <select id="hcKind">${filters.map(([v,n])=>`<option value="${v}" ${filter===v?'selected':''}>${n}</option>`).join('')}</select></label><label>筛选 <input id="hcSearch" placeholder="任务号 / 模型 / 状态" value="${escapeHtml(search)}"></label><span class="tiny muted">显示 ${list.length} 条，最多保留最近 ${entries.length} 条记录</span></div>`;
 const rows=list.map(x=>`<div class="history-item hc-entry">
  <div class="hc-entry-header"><b>${safeText(x.label)}</b><span class="hc-status">${safeText(x.status||'未知')}</span><span class="hc-date">${stamp(x.created_at)}</span></div>
  <div class="hc-entry-meta">${x.model?`<span>模型：${safeText(x.model)}</span>`:''}<span>本地编号：${safeText(x.id)}</span>${x.provider_id?`<span>供应商任务编号：${safeText(x.provider_id)}</span>`:''}</div>
  <div class="hc-entry-meta"><span>预估费用：${money(x.estimated_cost_usd)}</span><span>实际费用：${money(x.actual_cost_usd,'actual')}</span>${Number.isFinite(x.tokens)?`<span>词元数：${x.tokens}</span>`:''}${Number.isFinite(x.records)?`<span>采集记录：${x.records}</span>`:''}</div>
  ${x.error?`<div class="hc-error">${safeText(x.error)}</div>`:''}${x.source==='tasks'&&x.kind==='video'?`<div class="hc-prompt-controls"><button type="button" class="secondary" data-history-video-prompt="${escapeHtml(x.id)}">查看本次视频的历史提示词</button></div>`:''}</div>`).join('');
 const emptiness=(data.summary?.total_records||0)===0?
   '当前 data/studio.db 没有这些调用记录。如果旧版本确实产生过任务，请先检查历史数据库是否迁移到当前安装目录。':'所选条件下没有记录。';
 el.innerHTML=toolbar+(rows||`<div class="workspace-empty compact"><b>暂无匹配记录</b><span>${emptiness}</span></div>`)+`<div class="hc-note">${(data.notes||[]).map(safeText).join(' · ')}</div>`;
 el.querySelectorAll('[data-history-video-prompt]').forEach(btn=>btn.onclick=()=>showHistoryVideoPrompt(btn.dataset.historyVideoPrompt,btn.closest('.hc-entry')));
 byId('hcKind').onchange=e=>{filter=e.target.value;historyRows(cache)};
 byId('hcSearch').oninput=e=>{search=e.target.value.toLowerCase().trim();const start=e.target.selectionStart;historyRows(cache);const input=byId('hcSearch');input.focus();input.setSelectionRange(Math.min(start,input.value.length),Math.min(start,input.value.length))};
}
async function refreshUnifiedHistory(){
 const panel=byId('taskHistory');if(!panel)return;
 try{
  // Failure in video recovery must not hide other history records.
  const results=await Promise.allSettled([get('/api/history/overview?limit=220'),get('/api/tasks/recovery')]);
  if(results[0].status==='rejected')throw results[0].reason;
  cache=results[0].value;topLine(cache);historyRows(cache);
  const recovery=byId('taskRecovery');
  if(recovery){
   if(results[1].status==='fulfilled' && typeof window.renderRecoveryCenter==='function')window.renderRecoveryCenter(results[1].value?.rows||[]);
   else if(results[1].status==='fulfilled' && typeof renderRecoveryCenter==='function')renderRecoveryCenter(results[1].value?.rows||[]);
   else if(results[1].status==='rejected')recovery.innerHTML='<div class="muted">任务恢复状态查询失败，不影响已保存的历史记录。</div>';
  }
 }catch(e){panel.innerHTML='<div class="hc-error">历史数据读取失败：'+safeText(e.message)+'. 请检查当前工作台是否指向正确的 data/studio.db。</div>';}
}
// app.js uses the global named function; override only this page's rendering.
window.refreshHistory=refreshUnifiedHistory;
const style=document.createElement('style');style.textContent=`
#page-history .hc-stat-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:10px 0 15px}
#page-history .hc-stat{background:rgba(74,85,111,.16);border:1px solid #303b4e;padding:10px 12px;border-radius:10px;display:flex;flex-direction:column;gap:7px}
#page-history .hc-stat b{font-size:16px;overflow-wrap:anywhere;color:#f0f4ff}
#page-history .hc-stat small{font-size:12px;color:#a5b8d4}
#page-history .hc-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
#page-history .hc-chip{font-size:11px;padding:6px 9px;background:#222e44;color:#c4d4f1;border-radius:8px}
#page-history .hc-note{font-size:12px;color:#9faec8;line-height:1.8;margin:12px 0}
#page-history .hc-foot{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:11px;color:#acb8d0}
#page-history .hc-tools{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:6px 0 15px}
#page-history .hc-tools label{display:flex;gap:6px;align-items:center;min-width:0;font-size:12px}
#page-history .hc-tools select,#page-history .hc-tools input{min-width:90px;max-width:240px;padding:8px;background:#151e2d;color:#f1f5fe;border:1px solid #39475d;border-radius:8px}
#page-history .hc-entry{margin-bottom:10px;overflow-wrap:anywhere}
#page-history .hc-entry-header{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#page-history .hc-status{background:#323e55;padding:3px 7px;border-radius:7px;font-size:11px}
#page-history .hc-date{margin-left:auto;color:#a7bad6;font-size:11px}
#page-history .hc-entry-meta{display:flex;flex-wrap:wrap;gap:7px 16px;margin-top:8px;font-size:12px;color:#b5c5df}
#page-history .hc-error{margin-top:8px;color:#ffa6aa;font-size:12px}
@media(max-width:1200px){#page-history .hc-stat-grid{grid-template-columns:1fr 1fr}}
@media(max-width:680px){#page-history .hc-stat-grid{grid-template-columns:1fr}}
`;document.head.appendChild(style);
})();
