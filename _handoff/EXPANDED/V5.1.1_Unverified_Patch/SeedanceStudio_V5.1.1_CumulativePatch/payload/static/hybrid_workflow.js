/* V5.1.1: Mandatory official Hypit local build/export before OpenLux paid request.
   No silent fallback to the legacy FFmpeg-only replica. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
const enc=encodeURIComponent;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function req(url,body){const opts=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};const r=await fetch(url,opts);let j={};try{j=await r.json()}catch{}if(!r.ok||!j.ok)throw Error(j.error||`请求失败（${r.status}）`);return j.data;}
let ref=null,products=[],projectId=null,working=false,loading=false,tick=null,assets=[],currentStage='draft',snapshotKey='';
const input={ref:$('hcReference'),products:$('hcProducts'),prompt:$('hcPrompt'),button:$('hcGenerate')};
function progress(label,detail='',percent=0,error=false){$('hcProgress').innerHTML=`<strong class="${error?'hc-error':''}">${esc(label)}</strong><p>${esc(detail)}</p>`;$('hcMeterBar').style.width=Math.min(100,Math.max(0,percent))+'%';}
function steps(stage){let n=0;if(['ready','analyzing'].includes(stage))n=1;if(['generating','uncertain','failed'].includes(stage))n=2;if(['composing','interrupted','render_error'].includes(stage))n=3;if(stage==='completed')n=4;
 document.querySelectorAll('.hc-step').forEach((el,i)=>{el.classList.toggle('passed',i<n);el.classList.toggle('current',i===Math.min(2,Math.max(0,n-1))&&stage!=='draft');});}
function assetName(a){return a?.original_name||'未命名素材';}
function renderInputs(){let txt=ref?assetName(ref):'点击上传或拖入参考视频';$('hcRefName').textContent=txt;$('hcRefDrop').classList.toggle('has-file',!!ref);
 $('hcProductsList').innerHTML=products.length?products.map((a,i)=>`<span class="hc-product-tag" title="${esc(assetName(a))}">${esc(assetName(a))}<button type="button" data-remove="${i}" aria-label="移除此图片">×</button></span>`).join(''):'';
 $('hcProductsList').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{products.splice(Number(b.dataset.remove),1);renderInputs();});}
async function uploadFiles(files,allowed){const ff=[...files].filter(f=>allowed==='video'?f.type.startsWith('video/'):/^image\//.test(f.type));
 if(ff.length===0)throw Error('文件类型不匹配，请上传'+(allowed==='video'?'视频':'图片'));
 const fd=new FormData();ff.forEach(f=>fd.append('file',f,f.name));const r=await fetch('/api/upload',{method:'POST',body:fd});let j={};try{j=await r.json()}catch{}if(!r.ok||!j.ok)throw Error(j.error||`上传失败（${r.status}）`);return j.data||[];}
async function uploading(fn){if(working)return;working=true;input.button.disabled=true;try{await fn();}catch(e){progress('上传失败',e.message,0,true);}finally{working=false;input.button.disabled=false;}}
input.ref.addEventListener('change',()=>{const ff=[...input.ref.files||[]];if(!ff.length)return;uploading(async()=>{progress('正在上传参考视频','这一步只保存在本机，不调用付费模型',5);const out=await uploadFiles(ff.slice(0,1),'video');ref=out[0];projectId=null;currentStage='draft';renderInputs();steps('draft');progress('参考视频已上传','可继续上传商品图片，或直接开始复刻',0);});input.ref.value='';});
input.products.addEventListener('change',()=>{const ff=[...input.products.files||[]];if(!ff.length)return;uploading(async()=>{if(products.length+ff.length>30)throw Error('商品图片最多 30 张');progress('正在上传商品图片','上传完成后会显示在下方',6);const out=await uploadFiles(ff,'image');products=[...products,...out].slice(0,30);projectId=null;currentStage='draft';renderInputs();progress(`已添加 ${products.length} 张商品图`,'可以继续填写要求或直接开始复刻',0);});input.products.value='';});
function enableDrop(el,id){el.addEventListener('dragover',e=>{e.preventDefault();el.classList.add('dragover');});el.addEventListener('dragleave',()=>el.classList.remove('dragover'));
 el.addEventListener('drop',e=>{e.preventDefault();el.classList.remove('dragover');const files=[...(e.dataTransfer?.files||[])];if(!files.length)return;
 uploading(async()=>{const out=await uploadFiles(id==='ref'?files.slice(0,1):files,id==='ref'?'video':'image');if(id==='ref'){ref=out[0];}else{if(products.length+out.length>30)throw Error('商品图片最多 30 张');products.push(...out);}projectId=null;currentStage='draft';renderInputs();progress('素材已保存','可以开始复刻',0);});});
 el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.querySelector('input')?.click();}});}
enableDrop($('hcRefDrop'),'ref');enableDrop($('hcProductsDrop'),'products');
function stageLabel(stage){return ({draft:'等待开始',ready:'等待确认费用',analyzing:'分析参考视频',generating:'AI 视频生成中',composing:'本地合成中',completed:'生成完成',uncertain:'提交待核实',failed:'生成失败',render_error:'本地合成异常',interrupted:'本地任务中断',download_error:'等待恢复下载',needs_setup:'等待官方 Hypit 环境',manual_review:'官方工程需核验'})[stage]||stage;}
function showResult(x){window.__lastHypitReplica=x;currentStage=x.stage;steps(x.stage);$('hcRetryLocal').hidden=!!x.hypit_project_id||!['render_error','interrupted'].includes(x.stage);$('hcRecoverDownload').hidden=x.stage!=='download_error';const v=$('hcVideo'),d=$('hcDownload');if(x.output){if(v.getAttribute('src')!==x.output){v.src=x.output;v.load();}v.hidden=false;d.href=x.output;d.hidden=false;}else{v.hidden=true;d.hidden=true;}
 let pct=0;if(x.stage==='ready')pct=20;else if(x.stage==='generating')pct=48;else if(x.stage==='composing')pct=82;else if(x.stage==='completed')pct=100;else if(x.stage==='uncertain')pct=45;else if(x.stage==='render_error')pct=85;else if(x.stage==='download_error')pct=75;
 if(x.stage==='composing')pct=80+Math.round(Math.min(100,x.progress||0)*.18);
 progress(stageLabel(x.stage),x.error||x.message||'',pct,['uncertain','failed','render_error','download_error'].includes(x.stage));
 if(x.stage==='needs_setup')input.button.textContent='等待 Hypit 官方环境';else if(x.stage==='manual_review')input.button.textContent='请检查官方工程';else if(x.stage==='ready')input.button.textContent='继续复刻 · 预览费用';else if(x.stage==='completed')input.button.textContent='开始新的复刻';else if(['generating','composing','uncertain'].includes(x.stage))input.button.textContent='任务进行中';else input.button.textContent='开始复刻视频';
 if(!working)input.button.disabled=['generating','composing','uncertain','needs_setup','manual_review'].includes(x.stage);
}
async function status(){if(!projectId)return;const x=await req('/api/hybrid/simple/status?id='+enc(projectId));showResult(x);return x;}
async function history(){const list=await req('/api/hybrid/simple/projects');$('hcHistory').innerHTML=list.length?list.slice(0,12).map(x=>`<button type="button" data-hc-project="${esc(x.id)}" class="hc-history-item ${x.id===projectId?'selected':''}"><strong>${esc(x.name)}</strong><small>${new Date(x.updated_at*1000).toLocaleString('zh-CN',{hour12:false})}</small><span>查看进度 / 成片 →</span></button>`).join(''):'<div class="hc-empty">还没有复刻作品</div>';
 $('hcHistory').querySelectorAll('[data-hc-project]').forEach(b=>b.onclick=()=>restore(b.dataset.hcProject));}
async function restore(id){if(working)return;working=true;input.button.disabled=true;try{projectId=id;const x=await status();assets=await req('/api/assets');ref=assets.find(a=>a.id===x.reference_asset_id)||null;products=(x.product_asset_ids||[]).map(id=>assets.find(a=>a.id===id)).filter(Boolean);input.prompt.value=x.instruction||'';renderInputs();await history();}catch(e){progress('载入失败',e.message,0,true);}finally{working=false;input.button.disabled=['generating','composing','uncertain','needs_setup','manual_review'].includes(currentStage);}}
async function start(){if(working||['generating','composing','uncertain'].includes(currentStage))return;working=true;input.button.disabled=true;
 try{
  if(!ref)throw Error('请先上传一段参考视频');
  const hypitReady=await req('/api/hypit/official/health');
  if(!hypitReady.can_build)throw Error('官方 Hypit CLI 尚未通过检测。请先点击页面中的「一键准备官方 Hypit 环境」，完成安装后再提交付费视频生成。');
  const instruction=input.prompt.value.trim();let x=null;
  if(projectId){x=await req('/api/hybrid/simple/status?id='+enc(projectId));
   if(['completed','failed','interrupted','render_error'].includes(x.stage)){
     if(!confirm('这条复刻已经有任务记录。确定新建一条复刻吗？新的 AI 生成需要再次付费。'))return;
     projectId=null;x=null;
   } else if(x.instruction!==instruction||x.reference_asset_id!==ref.id||JSON.stringify(x.product_asset_ids)!==JSON.stringify(products.map(a=>a.id))){projectId=null;x=null;}
  }
  if(x?.task_id)throw Error('已存在付费视频生成任务，请等待 Hypit 官方构建，或更换参考素材新建任务；禁止重复扣费提交。');
  if(!projectId){progress('正在创建任务','只保存到本机，尚未扣费',8);
   const p=await req('/api/hybrid/simple/create',{reference_asset_id:ref.id,product_asset_ids:products.map(a=>a.id),instruction});projectId=p.id;
   await history();
  }
  x=await req('/api/hybrid/simple/status?id='+enc(projectId));
  let wvid=x.workflow_version_id;
  if(!wvid){steps('analyzing');progress('正在分析参考视频','自动识别画面切换、动作节奏和时间结构；根据视频长度可能需要几十秒。此阶段不消耗 OpenLux 视频生成费用。',15);
   const analyzed=await req('/api/workflow/analyze',{reference_asset_id:ref.id,product_asset_ids:products.map(a=>a.id),instruction});
   const gate=analyzed.analysis_gate||{};if(!gate.pass)throw Error(gate.reason||'视频结构证据不足，请检查参考素材，尚未扣费');
   await req('/api/workflow/confirm',{version_id:analyzed.version_id});
   await req('/api/hybrid/simple/link',{project_id:projectId,workflow_version_id:analyzed.version_id});
   wvid=analyzed.version_id;
  }
  steps('ready');progress('正在免费验证官方 Hypit','使用参考视频生成本地 2 秒样片，执行官方 check / runtime / plan / build / export。此阶段不会调用 OpenLux 视频生成。',23);
  const freeTest=await req('/api/hypit/official/preflight',{project_id:projectId,workflow_version_id:wvid});
  if(!freeTest.verified)throw Error('Hypit 免费全流程验证没有成功，已阻止付费');
  progress('Hypit 本地构建已通过，正在计算费用','官方本地验证成片已生成，可以进入 OpenLux 费用确认。',25);
  const preview=await req('/api/video/preview',{workflow_version_id:wvid,resolution:'720p',hypit_replica_project_id:projectId});
  const amount=preview.estimated_cost_usd==null?'OpenLux 具体费用未知（以账户实际账单为准）':'预估 $'+Number(preview.estimated_cost_usd).toFixed(3)+'（最终以 OpenLux 账单为准）';
  const priceMsg=`参考视频：${assetName(ref)}\n生成模型：${preview.model||'Seedance 2.5'}\n复刻时长：${preview.duration||'继承参考视频'}\n本次 OpenLux 模型调用：1 次\n${amount}\n\n确认后立即提交一笔付费生成，不会自动重试。是否继续？`;
  if(!confirm(priceMsg)){progress('已取消付费提交','视频分析结果已保存，下次点击「继续复刻」即可重新预览费用。',25);currentStage='ready';input.button.textContent='继续复刻 · 预览费用';return;}
  progress('正在提交 AI 视频生成','正在安全提交单次请求，请不要重复操作；刷新页面后也能恢复',38);
  const created=await req('/api/video/generate',{preview_token:preview.preview_token});
  if(!created?.task?.id)throw Error('接口没有返回任务 ID，请进入任务中心核对，不要重复提交付费请求');
  const recovered=await status();progress('已提交，AI 视频制作中',`任务 ${created.task.id} 已保存。完成后会自动在本地合成；无需一直打开这个页面。`,45);
  currentStage=recovered.stage;await history();
 }catch(e){progress('当前未能继续',e.message,25,true);if(projectId){try{const x=await req('/api/hybrid/simple/status?id='+enc(projectId));if(['generating','uncertain','composing','completed'].includes(x.stage))showResult(x);}catch{}}}
 finally{working=false;input.button.disabled=['generating','composing','uncertain','needs_setup','manual_review'].includes(currentStage);}
}
input.button.addEventListener('click',start);
$('hcRefresh').addEventListener('click',()=>status().catch(e=>progress('刷新失败',e.message,0,true)));
$('hcRecoverDownload').addEventListener('click',async()=>{if(!projectId||working)return;working=true;try{const info=await req('/api/hybrid/simple/status?id='+enc(projectId));if(!info.task_id)throw Error('当前任务记录缺少 ID，不能安全恢复');progress('正在恢复已有成片文件','只重新下载已生成的视频，不会再次调用视频生成模型',76);await req('/api/tasks/retry-download',{task_id:info.task_id});await status();}catch(e){progress('恢复下载失败',e.message,76,true);}finally{working=false;}});
$('hcRetryLocal').addEventListener('click',async()=>{if(!projectId||working)return;working=true;try{progress('开始重新本地合成','仅处理已有视频文件，不会再次调用 OpenLux',80);await req('/api/hybrid/simple/retry-local',{project_id:projectId});await status();}catch(e){progress('重新合成失败',e.message,80,true);}finally{working=false;}});
async function load(){if(loading)return;loading=true;try{assets=await req('/api/assets');const list=await req('/api/hybrid/simple/projects');if(list.length&&!projectId){projectId=list[0].id;const x=await status();ref=assets.find(a=>a.id===x.reference_asset_id)||null;products=(x.product_asset_ids||[]).map(id=>assets.find(a=>a.id===id)).filter(Boolean);input.prompt.value=x.instruction||'';renderInputs();}await history();}catch(e){progress('加载失败',e.message,0,true);}finally{loading=false;}}
async function officialHealth(){
 try{const h=await req('/api/hypit/official/health');
  const msg=h.installed?`官方 Hypit CLI 已检测到：${h.version_response||'版本未识别'}。本地工程将由官方 Runtime 构建。`:
    '尚未安装官方 Hypit CLI：AI 视频任务保留，系统不会使用仿制渲染器冒充 Hypit。点击下方按钮准备环境。';
  $('hcOfficialStatus').textContent=msg+(h.setup?.state==='running'?'\n正在下载官方依赖：'+(h.setup.log_tail||'请稍候').slice(-320):
    h.setup?.state==='failed'?'\n上次安装失败：'+(h.setup.log_tail||'请查看安装日志').slice(-400):'');
  $('hcSetupOfficial').hidden=h.installed || h.setup?.state==='running';
  return h;
 }catch(e){$('hcOfficialStatus').textContent='官方环境查询失败：'+e.message;return null;}
}
$('hcSetupOfficial').onclick=async()=>{
 if(!confirm('将从官方 npm 安装 Hypit CLI 和本地依赖，首次安装可能较久；不会自动调用 OpenLux 付费生成。是否开始？'))return;
 try{await req('/api/hypit/official/setup',{});$('hcOfficialStatus').textContent='官方 Hypit 安装已开始，可以在这个页面查看进度。';$('hcSetupOfficial').hidden=true;}
 catch(e){$('hcOfficialStatus').textContent='准备环境失败：'+e.message;}
};
$('hcRefreshOfficial').onclick=async()=>{
 $('hcOfficialUpdate').hidden=false;$('hcOfficialUpdate').textContent='正在查询 Hypit 官方版本（不安装）…';
 try{const d=await req('/api/hypit/official/update-check');
  $('hcOfficialUpdate').textContent=d.ok?`GitHub 最新版本：${d.latest}。当前程序不会直接覆盖升级，需要先通过兼容性测试。`:'版本检查失败：'+d.reason;
 }catch(e){$('hcOfficialUpdate').textContent='版本检查失败：'+e.message;}
 await officialHealth();
};
$('hcStudio').onclick=async()=>{
 const p=window.__lastHypitReplica?.hypit_project_id;
 if(!p)return;
 try{const d=await req('/api/hypit/official/studio',{project_id:p});window.open(d.url,'_blank','noopener');}
 catch(e){progress('打开 Hypit Studio 失败',e.message,85,true);}
};
const oldShow=showResult;
showResult=function(x){oldShow(x);$('hcStudio').hidden=!x.hypit_project_id||!x.output;};
let officialTick=0;
setInterval(()=>{if(document.querySelector('#page-hybrid.active')){officialTick++;if(officialTick%3===0)officialHealth();}},10000);
officialHealth();
// Automatic detection only: never download or install an unverified new Hypit release.
(async()=>{try{const k='seedance-hypit-last-version-check';const now=Date.now();
 const last=Number(localStorage.getItem(k)||0);if(now-last<86400000)return;
 const d=await req('/api/hypit/official/update-check');
 if(d.ok){localStorage.setItem(k,String(now));
 if(d.latest && d.latest!==('v'+(d.current||'').replace(/^v/,''))){
  $('hcOfficialUpdate').hidden=false;
  $('hcOfficialUpdate').textContent='Hypit 官方版本：'+d.latest+'（仅检测；待通过兼容性测试后才能更新）。';
 }}
}catch(_e){/* Not a reason to interrupt video creation. */}})();
function poll(){if(!document.querySelector('#page-hybrid.active')||working||!projectId)return;status().catch(()=>{});}
if(tick)clearInterval(tick);tick=setInterval(poll,5000);
window.HybridWorkflow={load};
})();
