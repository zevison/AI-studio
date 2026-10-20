/* V4.2.8.8: Bright Data verified task telemetry; ONLY the explicit submit button
   invokes the paid trigger. Timers query local jobs / existing provider snapshots. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
const api=async(path,body)=>{const opt=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
 const res=await fetch(path,{...opt,cache:'no-store'});let j;
 try{j=await res.json()}catch{throw Error('后台服务未返回有效结果，请检查本地工作台是否仍在运行')}
 if(!res.ok||!j.ok)throw Error(j.error||`HTTP ${res.status} 操作失败`);return j.data};
let lastStatusProduct=null;
let submitting=false,polling=false,refreshing=false,allJobs=[],lastPolling=new Map(),timer=null;
const statuses={submitting:'正在提交，等待服务商回执',running:'Bright Data 正在采集',ready:'采集完成，正在下载结果',done:'采集完成，已处理结果',failed:'Bright Data 采集失败',ready_partial:'部分成功，异常单独记录',provider_failed:'服务商返回错误数据',empty:'未找到有效视频',rejected:'请求被服务商拒绝',submission_unknown:'提交回执不确定，禁止再次付费提交',processing:'正在写入证据'};
const endStates=new Set(['done','failed','rejected','submission_unknown']);
const activeStates=new Set(['running','ready']);
function notify(text){if($('bdStatus'))$('bdStatus').textContent=text}
function selected(){return $('radarProduct')?.value||''}
function dt(t){return Number.isFinite(+t)&&+t>0?new Date(+t*1000).toLocaleString():'未知'}
function elapsed(t){if(!t)return '未知';const s=Math.max(0,Math.floor(Date.now()/1000-t));return s>=3600?`${Math.floor(s/3600)}小时${Math.floor((s%3600)/60)}分`:s>=60?`${Math.floor(s/60)}分${s%60}秒`:`${s}秒`}
function el(tag,cls,content){const n=document.createElement(tag);if(cls)n.className=cls;if(content!==undefined)n.textContent=String(content);return n}
function flow(current){const stages=['提交请求','服务商采集','获取结果','证据入库'];const n=current?.status;
 let upto=n==='submitting'?0:n==='running'?1:n==='ready'||n==='processing'?2:n==='done'?4:n==='failed'||n==='submission_unknown'||n==='rejected'?-1:0;
 const wrap=el('div','bd-stages');stages.forEach((s,i)=>{const node=el('span',i<upto?'done':i===upto?'active':'pending',s);wrap.append(node)});return wrap}
function headline(j){return statuses[j?.status]||j?.status||'暂无任务'}
function currentJob(){const id=selected();return allJobs.find(j=>j.product_id===id)||null}
function renderCurrent(){const box=$('bdCurrent');if(!box)return;box.replaceChildren();const j=currentJob();
 if(!selected())return void box.append(el('div','bd-line','请先在「商品知识中心」创建商品，然后返回选择'));
 if(!j){box.append(el('div','bd-line','未开始本商品的 Bright Data 采集（没有本地任务记录）'));box.append(el('div','bd-hint','点击「预览关键词与额度」不会扣费；只有人工确认后才会提交采集。'));return}
 const head=el('div','bd-live-head');head.append(el('strong','',headline(j)));head.append(el('span','bd-live-id','本地任务 '+j.id));box.append(head);
 box.append(flow(j));
 const details=el('div','bd-data');[
   `本次设定 ${j.quantity} 条（非成功保证）`,
   `已耗时 ${elapsed(j.created_at)}`,
   `服务商 ${j.remote_status|| (j.snapshot_id?'待首次查询':'尚无回执')}`,
   `实际返回 ${j.returned_records||0} 条`,
   `符合入库格式 ${j.actual_records||0} 条`,
   `新增入库 ${j.imported_records||0} 条`
 ].forEach(t=>details.append(el('span','',t)));box.append(details);
 if(j.snapshot_id)box.append(el('div','bd-hint','Bright Data 快照编号：'+j.snapshot_id));
 else if(j.status==='submitting')box.append(el('div','bd-hint','后台单次提交中；尚未收到快照编号。服务商未确认前不可重复发起。'));
 if(j.error)box.append(el('div','bd-error','失败原因：'+j.error));
 if(j.poll_error)box.append(el('div','bd-error','最近一次查询异常：'+j.poll_error+'；保留快照，可再次只读查询。'));
 if(j.provider_error_count)box.append(el('div','bd-error','服务商错误记录 '+j.provider_error_count+' 条（未作为视频入库）。'));
 if(j.raw_snapshot_file){const rb=el('button','secondary','免费重读本地快照');rb.type='button';rb.onclick=async()=>{
   try{const r=await api('/api/bright-tiktok/snapshot/restore',{id:j.id});notify(`免费重读完成：有效视频${r.valid_videos}，错误记录${r.provider_errors}`);await loadJobs();window.HotRadar?.load?.()}catch(e){notify('重读失败：'+e.message)}
 };box.append(rb)}
 if(j.status==='submission_unknown'&&!j.snapshot_id){const rb=el('button','secondary','补录已有快照编号（不采集）');rb.type='button';rb.onclick=async()=>{
   const id=prompt('仅填写 Bright Data 后台已确实存在的本次快照编号。此操作不会提交新采集：');if(!id)return;
   try{await api('/api/bright-tiktok/snapshot/attach',{id:j.id,snapshot_id:id.trim()});await loadJobs();notify('已有快照已关联，请点击只读查询任务状态')}catch(e){notify('关联失败：'+e.message)}
 };box.append(rb)}
 if(['done','ready_partial','provider_failed','empty'].includes(j.status))box.append(el('div','bd-hint',j.imported_records?'已完成入库。下方「热点证据」可查看，仍须人工核实内容与商品关联。':'服务商已结束；没有新增有效视频。可能无结果、仅图片帖子、错误记录或已入库。'));
 if(j.status==='submission_unknown')box.append(el('div','bd-error','⚠ 服务商是否接单尚无法确认。禁止自动重试，请先在 Bright Data 后台核实对应时间的任务和费用。'));
 if(j.status==='running'||j.status==='ready'){
   const b=el('button','secondary','立即检查任务状态（只读）');b.type='button';b.addEventListener('click',()=>pollOne(j.id,true));box.append(b);
   box.append(el('div','bd-hint',`自动查询本地状态每10秒一次；服务商状态最多每30秒查询一次。不会重新提交采集。${j.last_polled_at?'上次远端查询：'+dt(j.last_polled_at):''}`));
 }
 if(j.status==='submitting'&&Date.now()/1000-j.created_at>80)box.append(el('div','bd-error','提交时间异常偏长；后台将在重新读取时标记回执不确定，不会自动再次提交。'));
}
function renderHistory(){const box=$('bdJobs');if(!box)return;box.replaceChildren();const chosen=selected(),target=allJobs.filter(j=>j.product_id===chosen).slice(0,15);
 if(!target.length)return void box.append(el('p','tiny muted','此商品暂无历史采集记录'));
 for(const j of target){const div=el('div','bd-job');div.append(el('b','',headline(j)));
  div.append(el('div','tiny muted',`本地编号 ${j.id} · ${dt(j.created_at)} · 设定 ${j.quantity} 条 · 新增 ${j.imported_records||0} 条`));
  if(j.snapshot_id)div.append(el('div','tiny muted','快照：'+j.snapshot_id));
  if(j.error)div.append(el('div','bd-error',j.error));
  if(j.poll_error)div.append(el('div','bd-error',j.poll_error));
  if(j.provider_error_count)div.append(el('div','bd-error',`有 ${j.provider_error_count} 条服务商异常记录，已与正常视频隔离`));
  if(activeStates.has(j.status)){const b=el('button','secondary','查询状态（只读）');b.onclick=()=>pollOne(j.id,true);div.append(b)}box.append(div)}
}
function paint(){renderCurrent();renderHistory();window.HotRadar?.updateHarvest?.(allJobs)}
async function loadJobs(){allJobs=await api('/api/bright-tiktok/jobs');paint();return allJobs}
async function pollOne(id,explicit=false){if(polling)return;const j=allJobs.find(x=>x.id===id);if(!j||!activeStates.has(j.status))return;
 const now=Date.now(),last=lastPolling.get(id)||0;if(!explicit&&now-last<30000)return;
 polling=true;lastPolling.set(id,now);
 try{notify('正在只读查询 Bright Data 任务 '+id+'，不会发起新的采集…');await api('/api/bright-tiktok/poll',{id});await loadJobs();
  const fresh=allJobs.find(x=>x.id===id);notify(`任务 ${id}：${headline(fresh)}${fresh?.error?' · '+fresh.error:''}`);
  if(fresh?.status==='done')window.HotRadar?.load?.();
 }catch(e){notify('查询失败：'+e.message)}finally{polling=false}}
async function refresh(){if(refreshing)return;refreshing=true;
 try{const jobs=await loadJobs();const j=jobs.find(x=>x.product_id===selected()&&activeStates.has(x.status));
  if(j&&!polling)await pollOne(j.id);else if(!submitting&&!polling&&(lastStatusProduct!==selected()||$('bdStatus')?.textContent==='正在读取本地采集记录…'))notify('当前商品暂无进行中的采集。请预览关键词与额度，或展开历史任务。');lastStatusProduct=selected();
 }catch(e){notify('读取采集任务状态失败：'+e.message)}finally{refreshing=false}}
async function preview(){const p=await api('/api/bright-tiktok/preview',{product_id:selected(),quantity:+$('bdCount').value});
 notify('预计采集关键词：'+p.terms.join(' / ')+'；总上限 '+p.quantity+' 条（全部关键词共享）。仅预览，不扣费。'+(p.warning||''));return p}
async function submit(){if(submitting)return;submitting=true;const btn=$('bdSubmit');btn.disabled=true;
 try{await preview();if(!confirm('确认对 Bright Data 发起一次可能产生费用的采集任务？\n本次只提交一次；确认额度、超额限制后继续。'))return;
  notify('正在建立本地采集任务，请勿重复提交…');const r=await api('/api/bright-tiktok/submit',{product_id:selected(),quantity:+$('bdCount').value,confirmed:true});
  await loadJobs();notify(r.reused?`已复用已有任务 ${r.id}：${headline(r)}。本次没有再次付费提交。`:`已建立任务 ${r.id}，等待服务商返回快照编号。后台只提交一次。`);
 }catch(e){notify('采集未成功提交：'+e.message)}finally{submitting=false;btn.disabled=false}}
async function loadSettings(){try{const x=await api('/api/bright-tiktok/settings');$('bdFreeRemaining').value=x.free_remaining;$('bdConfirmedBilling').checked=false;
 $('bdSettingsStatus').textContent=(x.has_key?'密钥已保存；':'密钥未配置；')+'本地停止线 '+x.safe_limit+' 条；仅支持人工核对免费额度，当前未证实远端余额。'}catch(e){$('bdSettingsStatus').textContent=e.message}}
function bind(){if(!$('bdSubmit'))return;
 $('bdPreview').onclick=()=>preview().catch(e=>notify('预览失败：'+e.message));
 $('bdSubmit').onclick=submit;
 $('bdToSettings').onclick=()=>{document.querySelector('.nav[data-page="settings"]')?.click();$('bdApiKey')?.focus()};
 $('bdSaveSettings').onclick=async()=>{try{const data={api_key:$('bdApiKey').value,free_remaining:+$('bdFreeRemaining').value,billing_confirmed:$('bdConfirmedBilling').checked,ratio_confirmed:$('bdConfirmedBilling').checked};await api('/api/bright-tiktok/settings/save',data);$('bdApiKey').value='';$('bdSettingsStatus').textContent='已保存，人工额度确认30分钟内有效。'}catch(e){$('bdSettingsStatus').textContent=e.message}};
 $('bdBalanceCheck').onclick=async()=>{try{const r=await api('/api/bright-tiktok/balance');$('bdSettingsStatus').textContent='资金余额：'+(r.money_balance??'未知')+'；免费采集额度：本接口无法验证。'}catch(e){$('bdSettingsStatus').textContent=e.message}};
 $('radarProduct')?.addEventListener('change',()=>{paint();refresh()});
 loadSettings();refresh();timer=setInterval(()=>{if(document.querySelector('#page-radar')?.classList.contains('active'))refresh()},10000);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind);else bind();
window.BrightTK={load:loadJobs,refresh};
})();
