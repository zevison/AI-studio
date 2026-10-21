const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={status:null,videoRefs:[],replicaVideo:null,replicaProducts:[],replicaAudios:[],videoPreviewToken:null,replicaPreviewToken:null,workflowVersionId:null,workflowGroupId:null,workflowConfirmed:false,imageVersions:[],imageMode:'text',imageSourceAssets:[],deepMode:false,deepParentVersionId:null,currentImageAsset:null,taskTickers:{},taskPollers:{},restoredTasks:false,globalTasks:new Map(),globalTaskStatuses:new Map(),taskCenterInitialized:false,taskCenterUnread:0,taskCenterTimer:null,mentionMenu:null,costTimer:null,costData:null,draftTimer:null,draftRestoring:false,lastDraftSavedAt:null,assetLibrary:[],healthTimer:null,navigationTouched:false};
let videoSubmitInFlight=false;
let replicaSubmitInFlight=false;

function toast(msg,bad=false){const el=$('#toast');el.textContent=msg;el.style.borderColor=bad?'#8f2d46':'#3b455b';el.classList.add('show');setTimeout(()=>el.classList.remove('show'),3600)}
function providerErrorText(j){
 const raw=j?.provider_body;
 if(!raw)return '';
 let obj=null;try{obj=typeof raw==='string'?JSON.parse(raw):raw}catch{}
 const e=obj?.error||obj?.data?.error||obj;
 const msg=e?.message||e?.msg||e?.error?.message||obj?.message;
 const code=e?.code||e?.type||obj?.code;
 if(msg)return `${code?code+'：':''}${msg}`;
 const s=String(raw).replace(/\s+/g,' ').trim();
 return s.length>260?s.slice(0,260)+'…':s;
}
async function api(path,opts={}){
 const r=await fetch(path,opts);let j={};try{j=await r.json()}catch{}
 if(!r.ok||!j.ok){
   const detail=providerErrorText(j);
   const base=j.error||`请求失败 ${r.status}`;
   const e=new Error(detail?`${base}｜${detail}`:base);
   e.payload=j;throw e
 }
 return j.data
}
function post(path,data){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})})}

// V4.2.8.5: OS folder open is never part of a paid video-generation request.
// When local API is offline, show a copyable path rather than only "Failed to fetch".
function outputPathFallback(){
 const root=state.status?.root_dir;
 return root?String(root).replace(/[\\/]+$/,'')+'\\data\\outputs':'';
}
function showOutputPath(path,problem=''){
 let box=document.getElementById('outputPathPanel');
 if(!box){
   box=document.createElement('div');box.id='outputPathPanel';box.className='output-path-panel';
   box.setAttribute('role','status');document.body.appendChild(box);
 }
 box.replaceChildren();
 const title=document.createElement('div');title.className='output-path-title';
 title.textContent=problem?'无法自动打开文件夹':'本地视频输出目录';
 const note=document.createElement('div');note.className='output-path-note';
 note.textContent=problem?problem:'已发送打开请求；如果没有弹出资源管理器，可复制路径手动打开。';
 const inp=document.createElement('input');inp.className='output-path-value';
 inp.readOnly=true;inp.value=path||'无法获得目录路径，请查看当前工作台文件夹下的 data\\outputs';
 const buttons=document.createElement('div');buttons.className='output-path-actions';
 const copy=document.createElement('button');copy.className='secondary';copy.textContent='复制路径';
 copy.onclick=async()=>{
   try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(inp.value);
   else{inp.focus();inp.select();if(!document.execCommand('copy'))throw Error('copy blocked')}
   toast('输出路径已复制');
   }catch{inp.focus();inp.select();toast('已选中路径，请按 Ctrl+C 复制')}
 };
 const close=document.createElement('button');close.className='ghost';close.textContent='关闭';
 close.onclick=()=>box.remove();buttons.append(copy,close);
 box.append(title,note,inp,buttons);
}
async function openOutputFolder(){
 const ctrl=new AbortController();
 const timeout=setTimeout(()=>ctrl.abort(),8000);
 try{
   const d=await api('/api/open-output',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:ctrl.signal});
   showOutputPath(d?.path||outputPathFallback());
 }catch(e){
   const msg=e.name==='AbortError'?'本地服务响应超时；请复制路径手动打开。'
     :(e.message==='Failed to fetch'?'本地服务连接失败；请检查当前工作台是否仍在运行。':e.message);
   showOutputPath(outputPathFallback(),msg);
 }finally{clearTimeout(timeout)}
}
function esc(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function fmtTime(ts){return ts?new Date(ts*1000).toLocaleString():'-'}
function statusLabel(s){return ({pending:'等待中',running:'生成中',result_pending:'结果同步中',completed:'已完成',failed:'失败',submission_unknown:'提交结果不确定',reconciled_no_charge:'人工核账：未见扣费',reconciled_charged:'人工核账：已扣费'})[s]||s||'未知'}

function selectedTaskModel(id){const v=$(id)?.value;return v&&v!=='default'&&v!=='auto'?v:(state.status?.settings?.video_base_model||'doubao-seedance-2-5-260628')}
// Model-specific video resolution choices are fetched from the SAME backend
// capability matrix as the actual provider adapter, never guessed in the UI.
let resolutionProfiles=null;
const genericResolutionOptions=[
 {value:'480p',label:'480P'},{value:'540p',label:'540P'},{value:'720p',label:'720P'},
 {value:'768P',label:'768P'},{value:'1080p',label:'1080P'},{value:'2K',label:'2K'}];
function resolutionProfile(model){return resolutionProfiles?.[model]||null}
function profileModel(input){
 const raw=$(input)?.value||'';
 return (raw&&raw!=='auto'&&raw!=='default')?raw:(state.status?.settings?.video_base_model||'doubao-seedance-2-5-260628');
}
function closestSupportedResolution(current,items){
 if(items.some(i=>i.value===current))return current;
 const heights={'480p':480,'540p':540,'720p':720,'768P':768,'1080p':1080,'2K':1440};
 const target=heights[current]||720;
 // Prefer nearest output no lower than requested to avoid silently degrading
 // when 2K is not supported; preserve quality if a higher tier is available.
 const higher=items.filter(i=>(heights[i.value]||0)>=target).sort((a,b)=>heights[a.value]-heights[b.value]);
 if(higher.length)return higher[0].value;
 return [...items].sort((a,b)=>(heights[b.value]||0)-(heights[a.value]||0))[0]?.value||'720p';
}
function matchResolutionSelect(input,model,{silent=false,requested=null}={}){
 const select=$(input);if(!select)return;
 const hint=$(input==='#videoResolution'?'#videoResolutionHint':input==='#replicaResolution'?'#replicaResolutionHint':'#settingsResolutionHint');
 const profile=resolutionProfile(model),options=profile?.options||genericResolutionOptions;
 const previous=requested||select.value||'720p';
 const next=closestSupportedResolution(previous,options);
 select.replaceChildren(...options.map(item=>{const opt=document.createElement('option');opt.value=item.value;opt.textContent=item.label;return opt}));
 select.value=next;
 if(hint){const item=options.find(x=>x.value===next);hint.textContent=profile
  ? `${profile.model}｜${item.label}；实际生成档位 ${item.native}。${profile.note}`
  : '智能自动路由：尚未选出模型，当前展示候选档位；点击费用预览后确定实际生成规格。';}
 if(next!==previous&&!silent){
   const message=`清晰度已根据${model==='auto'?'智能路由':model}自动匹配：${previous} → ${next}，不会直接提交付费请求。`;
   toast(message);if(input==='#videoResolution')invalidatePreviews();else if(input==='#replicaResolution')invalidateReplicaPreparation('清晰度已匹配，重新预览后生成');
 }
 if(!silent&&next!==previous)scheduleDraftSave();
}
function updateResolutionControls({silent=false}={}){
 matchResolutionSelect('#videoResolution',profileModel('#videoModelChoice'),{silent});
 matchResolutionSelect('#replicaResolution',profileModel('#replicaModelChoice'),{silent});
 const settingChoice=$('#setVideoModelChoice')?.value;
 const settingModel=settingChoice==='custom'?$('#setVideoBaseModel')?.value:settingChoice;
 matchResolutionSelect('#setResolution',settingModel||'doubao-seedance-2-5-260628',{silent});
}
async function loadResolutionProfiles(){try{
 const result=await api('/api/video/resolution-options');resolutionProfiles=result.models||{};
 updateResolutionControls({silent:true});
}catch(e){console.warn('Model resolution profiles unavailable, using unfiltered preview choices',e)}}

function updateVideoRouteStatus(){ /* V3.9.8.20: task-level smart route controls removed */ }

async function checkNativeVideoModel(modelInput, outputInput){
 const model=selectedTaskModel(modelInput),out=$(outputInput);
 if(out)out.textContent='只读检查中，不会扣费……';
 try{
   const d=await post('/api/video/model-diagnostic',{model});
   if(out)out.textContent=(d.available?'模型在此密钥目录中可见：':'未在该密钥目录找到：')+model+'。'+(d.note||'');
   toast(d.available?'免费目录核验已完成（不等于付费生成成功）':'模型不可用；本次未调用付费接口',!d.available)
 }catch(e){if(out)out.textContent=e.message;toast('免费模型校验失败：'+e.message,true)}
}
$('#checkVideoModel')?.addEventListener('click',()=>checkNativeVideoModel('#videoModelChoice','#videoModelCheckText'));
$('#checkReplicaModel')?.addEventListener('click',()=>checkNativeVideoModel('#replicaModelChoice','#replicaModelCheckText'));

$('#setVideoModelChoice')?.addEventListener('change',()=>{const v=$('#setVideoModelChoice').value;$('#setVideoCustomModelWrap').hidden=v!=='custom';if(v!=='custom')$('#setVideoBaseModel').value=v;updateResolutionControls();});
[['#videoModelChoice','video'],['#replicaModelChoice','replica']].forEach(([sel,kind])=>{$(sel)?.addEventListener('change',()=>{const selected=$(sel)?.value||'';if(kind==='video'){invalidatePreviews();}else invalidateReplicaPreparation('复刻模型已变化，需要重新预览');updateVideoRouteStatus();updateResolutionControls();scheduleDraftSave();toast('已保存本次模型选择；将进行账户与接口校验，不会静默切换其他模型')})});

function activatePage(p,{save=true}={}){if(save)state.navigationTouched=true;if(p==='replica')p='hypit';const b=$(`.nav[data-page="${p}"]`);$$('.nav[data-page]').forEach(x=>x.classList.toggle('active',x===b));$$('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${p}`));$('#pageTitle').textContent=({hypit:'AI 视频制作 · Hypit',video:'视频创作',replica:'爆款复刻',image:'图片生成',assets:'本地素材库',radar:'热点收集',ads:'智能视频制作',products:'商品知识中心',history:'历史记录',settings:'状态与设置',guide:'使用指南',assistant:'GPT 对话'})[p]||'';if(p==='hypit'&&window.HypitStudio)window.HypitStudio.load();if(p==='radar'&&window.HotRadar)window.HotRadar.load();if(p==='ads'&&window.CreativeProduction)window.CreativeProduction.load();if(p==='products'&&window.ProductMemory)window.ProductMemory.load();if(p==='history')refreshHistory();if(p==='settings'){refreshActivity();refreshSystemHealth()}if(p==='assistant'&&window.GPTChat)window.GPTChat.load();if(p==='assets')refreshAssetLibrary();refreshCostHud(false);if(save)scheduleDraftSave()}
$$('.nav[data-page]').forEach(b=>b.onclick=()=>activatePage(b.dataset.page));

function setWorkspaceTab(group,tab){
  $$(`[data-workspace-tab^="${group}:"]`).forEach(b=>b.classList.toggle('active',b.dataset.workspaceTab===`${group}:${tab}`));
  $$(`[data-workspace-panel^="${group}:"]`).forEach(p=>p.classList.toggle('active',p.dataset.workspacePanel===`${group}:${tab}`));
}
$$('[data-workspace-tab]').forEach(b=>b.onclick=()=>{const [group,tab]=b.dataset.workspaceTab.split(':');setWorkspaceTab(group,tab)});
function taskTabState(group,task){const dot=$(`#${group==='replica'?'replica':'video'}TaskTabDot`);if(!dot)return;const active=['pending','running','result_pending','submission_unknown'].includes(task?.status);dot.classList.toggle('hidden',!active)}

function nearestCostRatio(w,h){
 const ww=Number(w||0),hh=Number(h||0);if(!(ww>0&&hh>0))return '9:16';
 const x=ww/hh;
 const ratios={'16:9':16/9,'4:3':4/3,'1:1':1,'3:4':3/4,'9:16':9/16,'21:9':21/9};
 return Object.entries(ratios).sort((a,b)=>Math.abs(a[1]-x)-Math.abs(b[1]-x))[0][0];
}
function currentCostContext(){
 const active=$('.page.active')?.id||'page-video';
 let duration=Number(state.status?.settings?.default_video_duration||10);
 let resolution=state.status?.settings?.default_video_resolution||'720p';
 let ratio=state.status?.settings?.default_video_ratio||'9:16';
 let inputVideoSeconds=0;
 if(active==='page-video'){
   duration=Number($('#videoDuration')?.value||duration);
   resolution=$('#videoResolution')?.value||resolution;
   ratio=$('#videoRatio')?.value||ratio;
   inputVideoSeconds=(state.videoRefs||[]).filter(x=>x?.kind==='video').reduce((s,x)=>s+Number(x.duration||0),0);
   if(ratio==='adaptive'){
     const anchor=(state.videoRefs||[]).find(x=>x&&(x.kind==='image'||x.kind==='video'));
     ratio=nearestCostRatio(anchor?.width,anchor?.height);
   }
 }else if(active==='page-replica'){
   duration=Number(state.replicaVideo?.duration||duration);
   resolution=state.status?.settings?.default_video_resolution||resolution;
   ratio=nearestCostRatio(state.replicaVideo?.width,state.replicaVideo?.height);
   inputVideoSeconds=Number(state.replicaVideo?.duration||0);
 }
 return {duration:Math.max(0,duration||0),resolution,ratio,inputVideoSeconds:Math.max(0,inputVideoSeconds||0)};
}
function money(v,digits=2){return v==null||!Number.isFinite(Number(v))?'--':Number(v).toFixed(digits)}
function renderCostHud(data){
 state.costData=data;
 const bal=data?.balance||{},est=data?.estimate||{},price=est?.model_price||{},fx=est?.fx||{};
 const b=$('#costBalanceTop'),p=$('#costModelPriceTop'),e=$('#costEstimateTop'),r=$('#costRemainingTop');

 if(b){
   b.className='';
   if(bal.available!=null){b.textContent=money(bal.available)+(bal.stale?'*':'');b.classList.add(bal.stale?'warn':'ok')}
   else{b.textContent=bal.needs_account_binding?'需绑定账户':'暂不可用';b.classList.add('warn')}
 }

 if(p){
   p.className='';
   if(price.ok&&price.billing==='token'){
     p.textContent=`${money(price.price_per_m,3)} / 1M Tokens`;
     const sourceMap={
       openlux_ratio_config:'OpenLux /api/ratio_config 实时倍率',
       openlux_ratio_config_model_price:'OpenLux /api/ratio_config 实时价格',
       openlux_pricing:'OpenLux /api/pricing Token 记录',
       verified_openlux_model_page:'OpenLux 模型详情页核验价'
     };
     p.title=(sourceMap[price.source]||price.source||'OpenLux Token 价格')
       +(price.verified_reference_price_per_m?`；模型页核验 ${price.verified_reference_price_per_m}/1M`:'');
     p.classList.add(price.source==='verified_openlux_model_page'?'warn':'ok');
   }else if(price.ok&&price.billing==='second'){
     p.textContent=`${money(price.price_per_second,4)} / 秒`;p.classList.add('ok')
   }else if(price.ok&&price.billing==='request'){
     p.textContent=`${money(price.price_per_request,4)} / 次`;p.classList.add('ok')
   }else{
     p.textContent='备用 ¥1/秒';p.classList.add('warn')
   }
 }

 if(e){
   e.className='';
   if(est.configured){
     if(est.fallback_used){
       e.textContent=`≈ $${money(est.cost_usd,2)}（¥${money(est.cost_cny,2)}）`;
       e.title=`备用估算：${est.formula||''}`;
     }else{
       e.textContent=`≈ ${money(est.cost,2)} / ${Number(est.output_seconds||0).toFixed((est.output_seconds||0)%1?1:0)}s`;
       e.title=`Token 估算：${est.formula||est.basis||''}`;
     }
     e.classList.add('warn');
   }else{
     e.textContent='无法估算';e.title=est?.formula||'';e.classList.add('warn');
   }
 }

 if(r)r.textContent=data?.remaining_conservative!=null?`约 ${data.remaining_conservative} 次`:'--';

 const cs=$('#costSettingsStatus');
 if(cs){
   const parts=[];
   parts.push(bal.available!=null?`账户钱包余额 ${money(bal.available)}`:'账户钱包余额尚未连接');
   if(!est.fallback_used&&price.ok&&price.billing==='token'){
     const sourceText=price.source==='verified_openlux_model_page'
       ?'模型详情页核验价'
       :price.source==='openlux_ratio_config'
         ?'/api/ratio_config 实时倍率'
         :'/api/pricing Token 报价';
     parts.push(`${sourceText} ${money(price.price_per_m,3)} / 1M Tokens`);
     if(est.estimated_tokens!=null)parts.push(`预计 ${Number(est.estimated_tokens).toLocaleString()} Tokens`);
     if(est.cost!=null)parts.push(`预计消耗 ${money(est.cost,4)}`);
     parts.push(`${est.width}×${est.height} · ${est.fps||24}fps · ${est.ratio}`);
   }else if(est.fallback_used){
     parts.push(`OpenLux Token 报价不可用，启用备用 ¥${money(est.cny_per_second,2)}/秒`);
     if(fx.rate!=null)parts.push(`CNY→USD ${Number(fx.rate).toFixed(6)}${fx.stale?'（缓存）':''}`);
     if(est.cost_usd!=null)parts.push(`预计 $${money(est.cost_usd,2)} / ¥${money(est.cost_cny,2)}`);
   }
   const budget=data?.budget;
   if(budget){
     if(budget.blocked){
       parts.push(`⚠ 本地付费保护已触发：${(budget.reasons||[]).join('；')}`);
     }else if(budget.enabled){
       const cap=budget.limit_usd>0?`$${money(budget.spent_usd)}/$${money(budget.limit_usd)}`:`$${money(budget.spent_usd)}（未设金额上限）`;
       const cnt=budget.max_tasks>0?` · ${budget.tasks}/${budget.max_tasks} 次`:` · ${budget.tasks} 次`;
       parts.push(`本地付费窗口 ${budget.window_hours} 小时：${cap}${cnt}${budget.unresolved_tasks?`，${budget.unresolved_tasks} 笔费用待核实`:''}`);
     }
   }
   cs.textContent=parts.join('；')+'。最终扣费以任务实际 usage.completion_tokens / OpenLux 调用日志为准。';
 }

 const os=$('#openLuxAccountStatus');
 if(os){
   if(bal.available!=null)os.innerHTML=`<span class="ok">已读取账户余额 ${esc(money(bal.available))}</span>${bal.username?' · '+esc(bal.username):''}${bal.group?' · 分组 '+esc(bal.group):''}`;
   else os.innerHTML='<span class="warn">尚未读取到账户钱包余额。</span> 账户余额只认 /api/user/self 的 data.quota。';
 }
}
async function refreshCostHud(force=false){if(!$('#costHud')||getComputedStyle($('#costHud')).display==='none')return;try{const c=currentCostContext();const q=new URLSearchParams({duration:String(c.duration),resolution:String(c.resolution),ratio:String(c.ratio),input_video_seconds:String(c.inputVideoSeconds)});if(force)q.set('force','1');const x=await api('/api/cost/live?'+q.toString());renderCostHud(x)}catch(e){const b=$('#costBalanceTop'),v=$('#costEstimateTop'),p=$('#costModelPriceTop');if(b){b.textContent='读取失败';b.className='bad'}if(v)v.textContent='--';if(p)p.textContent='--'}}
function scheduleCostHud(){clearTimeout(state._costDebounce);state._costDebounce=setTimeout(()=>refreshCostHud(false),180)}
function syncAccountBalanceBurst(){
  refreshCostHud(true);
  setTimeout(()=>refreshCostHud(true),3500);
  setTimeout(()=>refreshCostHud(true),12000);
}

async function refreshStatus(){try{
 const s=await api('/api/status');state.status=s;const t=s.tunnel||{},pa=s.public_assets||{},os=s.oss||{},cfg=s.settings||{};
 updateTaskCenterCapacity(Number(s.active_video_tasks||0),Number(s.max_concurrent_video_tasks||10));
 const topRoute=s.video_route_control==='provider_sort'?`${s.video_model} + provider.sort=${s.video_route_provider_sort}`:(s.video_route_control==='token_default'?`${s.video_model} · Token 默认`:s.video_model);
 $('#actualModelTop').textContent=topRoute;
 const pref=s.video_route_preference_label||'由 Token 设置决定';
 const pubLabel=pa.selected_provider==='aliyun_oss'?'阿里云 OSS 已就绪':(os.configured?'阿里云 OSS 已配置':(pa.fixed_url?'固定 HTTPS 已配置':(t.running?'Quick Tunnel 调试中':'未配置')));
 $('#sideStatus').innerHTML=`<div class="status-mini"><b>V${esc(s.version)}</b> · ${s.api_key_configured?'<span class="ok">OpenLux 已配置</span>':'<span class="bad">未配置密钥</span>'}<br><span class="muted">模型：${esc(s.video_base_model||'-')}</span><details><summary>更多运行状态</summary><div>路由：${esc(s.video_route_control_label||'-')}<br>实际请求：${esc(s.video_model)}<br>并发：${s.active_video_tasks}/${s.max_concurrent_video_tasks}<br>本地解析：${s.ffmpeg_available?'可用':'降级'}<br>素材公网：${esc(pubLabel)}</div></details></div>`;
 $('#settingsStatus').innerHTML=`<table class="preview-table"><tr><td>运行版本</td><td>V${esc(s.version)}</td></tr><tr><td>基础视频模型</td><td>${esc(s.video_base_model)}</td></tr><tr><td>路由控制方式</td><td>${esc(s.video_route_control_label||'-')}</td></tr><tr><td>路由目标</td><td>${esc(pref)}</td></tr><tr><td>provider.sort</td><td>${esc(s.video_route_provider_sort||'未写入请求')}</td></tr><tr><td>实际 model 字段</td><td>${esc(s.video_model)}</td></tr><tr><td>创建接口</td><td>${esc(s.video_create_path)}</td></tr><tr><td>查询接口</td><td>${esc(s.video_query_path)}</td></tr><tr><td>图片模型</td><td>${esc(s.image_model)}</td></tr><tr><td>Hypit 本地工作流引擎</td><td>${esc(s.hypit_engine||'native-v1')}</td></tr><tr><td>FFmpeg 视频解析</td><td>${s.ffmpeg_available?'可用':'降级模式'}</td></tr><tr><td>重复拦截</td><td>${s.dedupe_seconds}s</td></tr><tr><td>视频并发上限</td><td>${s.max_concurrent_video_tasks}</td></tr><tr><td>当前服务端口</td><td>${esc(s.port)}</td></tr><tr><td>公网素材默认通道</td><td>${os.configured?'阿里云 OSS（已配置）':'阿里云 OSS（未配置）'}</td></tr><tr><td>OSS Endpoint</td><td>${esc(os.endpoint||'-')}</td></tr><tr><td>OSS Region</td><td>${esc(os.region||'未识别')}</td></tr><tr><td>OSS Bucket</td><td>${esc(os.bucket||'-')}</td></tr><tr><td>临时链接有效期</td><td>${esc(os.ttl_minutes||'-')} 分钟</td></tr></table>`;
 const providerLine=s.video_route_provider_sort?`<br>请求 provider.sort：<b>${esc(s.video_route_provider_sort)}</b>`:'';
 const tokenLine=s.video_route_control==='token_default'?'<br><span class="warn">本地请求不指定路由；实际优先级由 OpenLux Token 设置页的 routing_priority 决定。</span>':'';
 $('#effectiveRoute').innerHTML=`<b>当前实际生效</b><br>基础模型：${esc(s.video_base_model)}<br>控制方式：${esc(s.video_route_control_label||'-')}<br>路由目标：${esc(pref)}<br>实际 model 字段：<b>${esc(s.video_model)}</b>${providerLine}${tokenLine}<br>创建接口：${esc(s.video_create_path)}<br>查询接口：${esc(s.video_query_path)}`;
 const cred=[];cred.push(`AccessKey ID：${os.oss_access_key_configured?'<span class="ok">已配置 '+esc(os.oss_access_key_masked||'')+'</span>':'<span class="bad">未配置</span>'}`);cred.push(`AccessKey Secret：${os.oss_access_key_secret_configured?'<span class="ok">已配置</span>':'<span class="bad">未配置</span>'}`);$('#ossCredentialStatus').innerHTML=cred.join('　');
 applySettingsToUi(cfg);refreshCostHud(false)
}catch(e){toast(e.message,true)}}
function applySettingsToUi(c){if(!c)return;const set=(id,v)=>{const e=$(id);if(e&&document.activeElement!==e)e.value=v??''};
 const baseModel=c.video_base_model||'doubao-seedance-2-5-260628';const msel=$('#setVideoModelChoice');if(msel){const available=[...msel.options].some(o=>o.value===baseModel);set('#setVideoModelChoice',available?baseModel:'custom');$('#setVideoCustomModelWrap').hidden=available;}set('#setVideoBaseModel',baseModel);
set('#setVideoRouteControl',c.video_route_control||'model_suffix');set('#setVideoRoutePreference',c.video_route_preference||'success_rate');set('#setEndpointStrategy',c.video_endpoint_strategy||'recommended');set('#setVideoCreatePath',c.custom_video_create_path||'');set('#setVideoQueryPath',c.custom_video_query_path||'');
 updateResolutionControls({silent:true});
 set('#setVideoMode',c.default_video_mode);set('#setVideoRatio',c.default_video_ratio);set('#setResolution',c.default_video_resolution);set('#setDuration',c.default_video_duration);set('#setImageRatio',c.default_image_ratio);set('#setImageQuality',c.image_quality);set('#setDedupeSeconds',c.dedupe_seconds);set('#setMaxConcurrent',c.max_concurrent_video_tasks);set('#setHttpTimeout',c.http_timeout);set('#setQueryRetries',c.query_retries);
 set('#setCostManualBalance',c.cost_manual_balance);set('#setCostQuotaPerUsd',c.cost_quota_per_usd);set('#setCostFallbackCnyPerSecond',c.cost_fallback_cny_per_second||1);set('#setCostFxRefreshMinutes',c.cost_fx_refresh_minutes||30);set('#setPaidBudgetUsd',c.paid_budget_usd);set('#setPaidBudgetWindowHours',c.paid_budget_window_hours||24);set('#setPaidBudgetMaxTasks',c.paid_budget_max_tasks);set('#setCost720Low',c.cost_video_720_low_per_second);set('#setCost720High',c.cost_video_720_high_per_second);set('#setCost480Multiplier',c.cost_video_480_multiplier);set('#setCost1080Multiplier',c.cost_video_1080_multiplier);const cab=$('#setCostAutoBalance');if(cab)cab.checked=true;
 set('#setSceneThreshold',c.replica_scene_threshold);set('#setMaxScenes',c.replica_max_scenes);set('#setTranscript',c.replica_transcript_mode);set('#setOcr',c.replica_ocr_mode);set('#setPublicStrategy',c.public_asset_strategy||'oss_auto');set('#setFixedPublicUrl',c.fixed_public_asset_base_url);set('#setAssetTtl',c.asset_link_ttl_minutes);set('#setOSSEndpoint',c.oss_endpoint||'');set('#setOSSBucket',c.oss_bucket||'');set('#setOSSPrefix',c.oss_object_prefix||'seedance-studio');set('#setOSSDeleteHours',c.oss_delete_after_hours||24);
 [['#setInheritDuration','replica_inherit_duration'],['#setInheritRatio','replica_inherit_ratio'],['#setAnalysisGate','replica_require_analysis_gate'],['#setWordAnchorGate','replica_require_word_anchor_for_paid'],['#setSpatialAnalysis','replica_spatial_analysis'],['#setAutoQualityCheck','auto_quality_check'],['#setTunnelSelftest','tunnel_selftest_before_paid'],['#setOSSAutoCleanup','oss_auto_cleanup']].forEach(([id,k])=>{const e=$(id);if(e)e.checked=!!c[k]});
 syncRouteUi();
 if(!state._defaultsApplied){$('#videoMode').value=c.default_video_mode||'auto';$('#videoRatio').value=c.default_video_ratio||'9:16';$('#videoResolution').value=c.default_video_resolution||'720p';$('#videoDuration').value=c.default_video_duration||10;$('#imageRatio').value=c.default_image_ratio||'9:16';state._defaultsApplied=true;updateModeUi();updateResolutionControls({silent:true})}}

function syncRouteUi(){
 const endpointCustom=$('#setEndpointStrategy')?.value==='custom';
 $$('.advanced-endpoint').forEach(x=>x.classList.toggle('hidden',!endpointCustom));
 const control=$('#setVideoRouteControl')?.value||'model_suffix';
 const prefWrap=$('#routePreferenceWrap');
 if(prefWrap)prefWrap.classList.toggle('hidden-setting',control==='token_default');
 const pref=$('#setVideoRoutePreference')?.value||'success_rate';
 if(control!=='token_default'&&pref==='price') toast('当前路由目标为“价格最低”。正式生成默认更建议“成功率最高”。');
 if(control==='token_default') toast('Token 级默认不会在请求里写路由参数，请确认 OpenLux Token 的 routing_priority 已配置。');
}
$('#setVideoRouteControl')?.addEventListener('change',syncRouteUi);$('#setVideoRoutePreference')?.addEventListener('change',syncRouteUi);$('#setEndpointStrategy')?.addEventListener('change',syncRouteUi);

async function uploadFileList(fileList){const files=[...(fileList||[])].filter(Boolean);if(!files.length)throw new Error('没有识别到可上传文件');const fd=new FormData();files.forEach((f,i)=>{const fallback=`clipboard_${Date.now()}_${i}`;fd.append('file',f,f.name||fallback)});const res=await fetch('/api/upload',{method:'POST',body:fd});const j=await res.json();if(!j.ok)throw new Error(j.error||'上传失败');return j.data}
async function uploadFiles(input){const files=[...input.files];const data=await uploadFileList(files);input.value='';return data}
window.addEventListener('seedance:viral-reference',e=>{
 const a=e.detail?.asset;
 if(a?.kind!=='video'||!a.id)return;
 if(e.detail?.replace===true)state.videoRefs=[a];else if(!state.videoRefs.some(x=>x.id===a.id))state.videoRefs.push(a);
 renderAssets();toast('真实参考视频已加入视频创作的参考素材；生成前请检查素材与模型。');
});
function assetPreview(a){if(a.kind==='image')return `<img src="${a.url}" alt="${esc(a.original_name)}">`;if(a.kind==='video')return `<video src="${a.url}" muted preload="metadata"></video>`;if(a.kind==='audio')return `<audio src="${a.url}" controls preload="metadata"></audio>`;return '<div class="empty-state">文件</div>'}
function kindLabel(kind){return ({image:'图片',video:'视频',audio:'音频'})[kind]||kind||'素材'}
// The editor speaks @image 1 / @video 1 / @audio 1. Older drafts are converted
// on restoration, while the server still accepts the old tokens safely.
function normalizePromptMentions(text){return String(text||'').replace(/@(image|video|audio|图片|视频|音频)\s*(\d+)(?!\d)/gi,(_,k,n)=>`@${({图片:'image',视频:'video',音频:'audio'})[k]||k.toLowerCase()} ${Number(n)}`)}
function escapeMentionRegex(text){return text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}
function refreshRestoredMentions(){['#videoPrompt','#replicaInstruction','#imagePrompt'].forEach(selector=>{const el=$(selector);if(el)el.value=normalizePromptMentions(el.value)})}

function mentionScopeAssets(scope){if(scope==='video')return state.videoRefs||[];if(scope==='replica')return [state.replicaVideo,...(state.replicaProducts||[]),...(state.replicaAudios||[])].filter(Boolean);if(scope==='image'){const out=[];for(const a of [...(state.imageSourceAssets||[]),state.currentImageAsset].filter(Boolean)){if(!out.some(x=>x.id===a.id))out.push(a)}return out}return []}
function buildAliasRecords(list,scope='video'){const counts={image:0,video:0,audio:0};return (list||[]).filter(Boolean).map((a,i)=>{counts[a.kind]=(counts[a.kind]||0)+1;return {...a,__uploadIndex:i,__alias:`@${({image:'image',video:'video',audio:'audio'})[a.kind]||a.kind} ${counts[a.kind]}`,__providerAlias:`@${({image:'image',video:'video',audio:'audio'})[a.kind]||a.kind}${counts[a.kind]}`,__kindIndex:counts[a.kind]}})}
function scopeAliasPayload(scope){return Object.fromEntries(buildAliasRecords(mentionScopeAssets(scope),scope).map(a=>[a.id,a.__alias]))}
function mentionRecords(scope){return buildAliasRecords(mentionScopeAssets(scope),scope)}
function scopeInput(scope){return scope==='video'?$('#videoPrompt'):scope==='replica'?$('#replicaInstruction'):$('#imagePrompt')}
function rewriteAliasesAfterAssetChange(scope,beforeAssets,afterAssets,deletedIds=[]){const input=scopeInput(scope);if(!input)return;const oldRecs=buildAliasRecords(beforeAssets,scope),newRecs=buildAliasRecords(afterAssets,scope);const oldById=Object.fromEntries(oldRecs.map(r=>[r.id,r.__alias]));const newById=Object.fromEntries(newRecs.map(r=>[r.id,r.__alias]));let text=normalizePromptMentions(input.value);const placeholders=[];for(const [id,oldAlias] of Object.entries(oldById)){if(deletedIds.includes(id)){text=text.replace(new RegExp(escapeMentionRegex(oldAlias)+'(?!\\d)','g'),`【已删除素材:${oldAlias.slice(1)}】`);continue}const newAlias=newById[id];if(newAlias&&newAlias!==oldAlias){const ph=`__SEEDANCE_ALIAS_${placeholders.length}__`;placeholders.push([ph,newAlias]);text=text.replace(new RegExp(escapeMentionRegex(oldAlias)+'(?!\\d)','g'),ph)}}for(const [ph,val] of placeholders)text=text.split(ph).join(val);if(text!==input.value){input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}))}}
function currentMentionToken(input){const caret=input.selectionStart??input.value.length;const before=input.value.slice(0,caret);const m=before.match(/@[^\s@，。；;,.!?！？:：()（）\[\]{}]{0,40}$/u);if(!m)return null;return {start:caret-m[0].length,end:caret,text:m[0],query:m[0].slice(1).toLowerCase()}}
function positionMentionMenu(input,menu){
 const r=input.getBoundingClientRect();const edge=10;
 const width=Math.min(Math.max(280,Math.min(r.width,510)),Math.max(200,window.innerWidth-2*edge));
 menu.style.width=`${width}px`;
 menu.style.left=`${Math.max(edge,Math.min(r.left,window.innerWidth-width-edge))}px`;
 menu.style.top='0px';menu.style.visibility='hidden';menu.classList.add('show');
 const availableAbove=Math.max(0,r.top-edge-6), availableBelow=Math.max(0,window.innerHeight-r.bottom-edge-6);
 const useBelow=availableBelow>=Math.min(260,availableAbove)||availableBelow>=availableAbove;
 const space=useBelow?availableBelow:availableAbove;
 // Keep the inner list scrollable: do not let the parent page's scroll handler close the menu.
 const maxHeight=Math.max(150,Math.min(500,space || window.innerHeight-2*edge));
 menu.style.maxHeight=`${maxHeight}px`;
 menu.style.top=`${Math.max(edge,Math.min(useBelow?r.bottom+6:r.top-maxHeight-6,window.innerHeight-maxHeight-edge))}px`;
 menu.style.visibility='visible';
}
function adjustMentionMenu(){const m=state.mentionMenu;if(m?.el&&m.input?.isConnected)positionMentionMenu(m.input,m.el)}
function closeMentionMenu(){const m=state.mentionMenu;if(m?.el)m.el.classList.remove('show');state.mentionMenu=null}
function applyMentionSelection(input,scope,record,token){const t=token||currentMentionToken(input);if(!t)return;let alias=record.__alias;if(scope==='image'&&record.kind==='image'){state.imageSourceAssets=[record];alias='@image 1';if(!state.deepMode)setImageMode('image');renderImageSourceList()}const before=input.value.slice(0,t.start),after=input.value.slice(t.end);input.value=before+alias+' '+after;const pos=(before+alias+' ').length;input.setSelectionRange(pos,pos);input.focus();input.dispatchEvent(new Event('input',{bubbles:true}));closeMentionMenu()}
function renderMentionMenu(input,scope,token){const all=mentionRecords(scope);const q=(token?.query||'').toLowerCase();const filtered=all.filter(r=>!q||r.__alias.toLowerCase().includes('@'+q)||String(r.original_name||'').toLowerCase().includes(q)||kindLabel(r.kind).includes(q));if(!filtered.length){closeMentionMenu();return}let el=$('#mentionPicker');if(!el){el=document.createElement('div');el.id='mentionPicker';el.className='mention-picker';document.body.appendChild(el)}el.innerHTML=`<div class="mention-picker-head"><b>选择参考素材</b><span>${filtered.length} 个可用素材</span></div><div class="mention-picker-list">${filtered.map((r,i)=>`<button type="button" class="mention-option${i===0?' active':''}" data-mention-index="${i}"><span class="mention-option-icon">${r.kind==='image'?'图':r.kind==='video'?'视':'音'}</span><span class="mention-option-main"><b>${esc(r.__alias)}</b><small>${esc(r.original_name||kindLabel(r.kind))}${r.duration?` · ${Number(r.duration).toFixed(2)}s`:''}</small></span><span class="mention-option-type">${esc(kindLabel(r.kind))}</span></button>`).join('')}</div>`;state.mentionMenu={el,input,scope,token,items:filtered,index:0};positionMentionMenu(input,el);$$('#mentionPicker [data-mention-index]').forEach(btn=>btn.onclick=()=>{const idx=Number(btn.dataset.mentionIndex);applyMentionSelection(input,scope,filtered[idx],token)});}
function refreshMentionMenu(input,scope){const token=currentMentionToken(input);if(!token){if(state.mentionMenu?.input===input)closeMentionMenu();return}renderMentionMenu(input,scope,token)}
function bindMentionInput(selector,scope){const input=$(selector);if(!input)return;input.addEventListener('blur',()=>{const next=normalizePromptMentions(input.value);if(next!==input.value){input.value=next;input.dispatchEvent(new Event('input',{bubbles:true}))}});input.addEventListener('input',()=>refreshMentionMenu(input,scope));input.addEventListener('click',()=>{const token=currentMentionToken(input);if(token)renderMentionMenu(input,scope,token)});input.addEventListener('keydown',e=>{const m=state.mentionMenu;if(!m||m.input!==input)return;if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();m.index=(m.index+(e.key==='ArrowDown'?1:-1)+m.items.length)%m.items.length;$$('#mentionPicker .mention-option').forEach((x,i)=>{x.classList.toggle('active',i===m.index);if(i===m.index)x.scrollIntoView({block:'nearest'});});return}if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();applyMentionSelection(input,scope,m.items[m.index],m.token);return}if(e.key==='Escape'){e.preventDefault();closeMentionMenu()}})}
function insertAliasIntoScope(scope,alias){const input=scope==='video'?$('#videoPrompt'):scope==='replica'?$('#replicaInstruction'):$('#imagePrompt');if(!input)return;const pos=input.selectionStart??input.value.length;const sep=pos>0&&!/\s$/.test(input.value.slice(0,pos))?' ':'';input.value=input.value.slice(0,pos)+sep+alias+' '+input.value.slice(pos);const next=pos+sep.length+alias.length+1;input.setSelectionRange(next,next);input.focus();input.dispatchEvent(new Event('input',{bubbles:true}))}
function bindMentionChipClicks(){$$('[data-mention-insert]').forEach(b=>b.onclick=e=>{e.preventDefault();insertAliasIntoScope(b.dataset.mentionScope,b.dataset.mentionInsert)})}
document.addEventListener('pointerdown',e=>{if(state.mentionMenu?.el&&!state.mentionMenu.el.contains(e.target)&&e.target!==state.mentionMenu.input)closeMentionMenu()});
window.addEventListener('resize',adjustMentionMenu);
// Scrolling inside the dropdown must NEVER close it. Only reposition it if an
// ancestor of the textarea scrolls. Popup wheel/drag/touch stays native.
window.addEventListener('scroll',e=>{const m=state.mentionMenu;if(!m)return;if(e.target===m.el||m.el.contains(e.target))return;adjustMentionMenu()},true);
function parseFrameBindings(mode,prompt,records){
  const text=normalizePromptMentions(prompt);
  const byAlias=Object.fromEntries((records||[]).map(r=>[r.__alias,r]));
  const images=(records||[]).filter(r=>r.kind==='image');
  const clauses=text.split(/[。；;\n]/);
  const findAlias=(words)=>{
    for(const clause of clauses){
      if(!words.some(w=>clause.includes(w)))continue;
      const aliases=clause.match(/@image \d+/g)||[];
      if(aliases.length)return aliases[0];
    }
    return null;
  };
  let firstAlias=findAlias(['首帧','第一帧','开场帧','开头帧']);
  let lastAlias=findAlias(['尾帧','最后一帧','结尾帧','结束帧']);
  const pair=text.match(/首尾帧[^。；;\n]{0,30}?(@image \d+)[^。；;\n]{0,12}?(?:和|及|与|、|,|，)[^。；;\n]{0,12}?(@image \d+)/);
  if(pair){if(!firstAlias)firstAlias=pair[1];if(!lastAlias)lastAlias=pair[2]}
  if(firstAlias&&!byAlias[firstAlias])firstAlias=null;
  if(lastAlias&&!byAlias[lastAlias])lastAlias=null;
  if(mode==='first_frame'){
    if(!firstAlias&&images[0])firstAlias=images[0].__alias;
  }else if(mode==='first_last'){
    if(!firstAlias&&images[0])firstAlias=images[0].__alias;
    if(!lastAlias&&images[1])lastAlias=images[1].__alias;
    if(firstAlias&&lastAlias&&firstAlias===lastAlias){lastAlias=images.find(x=>x.__alias!==firstAlias)?.__alias||null}
  }
  return {firstAlias,lastAlias};
}
function assetCard(a,scope,meta={}){const dur=a.duration?` · ${Number(a.duration).toFixed(2)}s`:'';const mentionScope=meta.mentionScope||scope;const alias=meta.alias?`<button type="button" class="alias-pill mention-chip" data-mention-scope="${esc(mentionScope)}" data-mention-insert="${esc(meta.alias)}" title="点击插入到提示词">${esc(meta.alias)}</button>`:'';const role=meta.role&&meta.role!=='普通参考'?`<span class="role-pill">${esc(meta.role)}</span>`:'';return `<div class="asset-card" data-asset="${esc(a.id)}">${assetPreview(a)}<button class="remove" data-remove="${scope}:${esc(a.id)}">×</button><div class="asset-meta">${alias}${role}</div><small class="asset-title">${esc(a.original_name)} · ${esc(kindLabel(a.kind))}${dur}</small></div>`}
function selectedAudioReferenceMode(scope){
 const files=scope==='video'?state.videoRefs:state.replicaAudios;
 if(!(files||[]).some(x=>x?.kind==='audio'))return null;
 return document.querySelector(`input[name="${scope==='video'?'videoAudioUse':'replicaAudioUse'}"]:checked`)?.value||'timbre';
}
for(const name of ['videoAudioUse','replicaAudioUse']){
 document.querySelectorAll(`input[name="${name}"]`).forEach(radio=>radio.addEventListener('change',()=>{
   if(name==='replicaAudioUse')invalidateReplicaPreparation('音频使用方式已改变，请重新分析并准备生成。');
   invalidatePreviews();scheduleDraftSave();
 }));
}
function renderAssets(){
  const records=buildAliasRecords(state.videoRefs,'video');const frame=parseFrameBindings($('#videoMode')?.value,$('#videoPrompt')?.value,records);const roleMap={};if(frame.firstAlias)roleMap[frame.firstAlias]='首帧';if(frame.lastAlias)roleMap[frame.lastAlias]='尾帧';
  $('#videoRefList').innerHTML=records.map(a=>assetCard(a,'video',{alias:a.__alias,role:roleMap[a.__alias]||'普通参考',mentionScope:'video'})).join('') || '<div class="muted tiny">还没有参考素材。上传后输入 @ 即可选择图片 / 视频 / 音频。</div>';
  const replicaRecords=buildAliasRecords(mentionScopeAssets('replica'),'replica');const replicaMap=Object.fromEntries(replicaRecords.map(r=>[r.id,r.__alias]));
  $('#replicaVideoInfo').innerHTML=state.replicaVideo?assetCard(state.replicaVideo,'replica-video',{alias:replicaMap[state.replicaVideo.id]||'@video 1',mentionScope:'replica'}):'';
  $('#replicaProductList').innerHTML=state.replicaProducts.map(a=>assetCard(a,'replica-product',{alias:replicaMap[a.id],mentionScope:'replica'})).join('');
  $('#replicaAudioList').innerHTML=state.replicaAudios.map(a=>assetCard(a,'replica-audio',{alias:replicaMap[a.id],mentionScope:'replica'})).join('')||'<span class="tiny muted">尚未添加参考音频，选择音频后可通过 @audio 1 引用。</span>';
  // New semantic audio options are only visible while this task actually has uploaded audio.
  const videoHasAudio=state.videoRefs.some(x=>x?.kind==='audio');
  const replicaHasAudio=state.replicaAudios.some(x=>x?.kind==='audio');
  const videoUse=$('#videoAudioUse'),replicaUse=$('#replicaAudioUse');
  if(videoUse)videoUse.hidden=!videoHasAudio;
  if(replicaUse)replicaUse.hidden=!replicaHasAudio;
  $$('[data-remove]').forEach(b=>b.onclick=()=>{const [scope,id]=b.dataset.remove.split(':');if(scope==='video'){const before=[...state.videoRefs];state.videoRefs=state.videoRefs.filter(x=>x.id!==id);rewriteAliasesAfterAssetChange('video',before,state.videoRefs,[id])}if(scope==='replica-video'){const before=mentionScopeAssets('replica');state.replicaVideo=null;rewriteAliasesAfterAssetChange('replica',before,mentionScopeAssets('replica'),[id])}if(scope==='replica-product'){const before=mentionScopeAssets('replica');state.replicaProducts=state.replicaProducts.filter(x=>x.id!==id);rewriteAliasesAfterAssetChange('replica',before,mentionScopeAssets('replica'),[id])}if(scope==='replica-audio'){const before=mentionScopeAssets('replica');state.replicaAudios=state.replicaAudios.filter(x=>x.id!==id);rewriteAliasesAfterAssetChange('replica',before,mentionScopeAssets('replica'),[id]);invalidateReplicaPreparation('参考音频已经变化，请重新分析并准备生成。')}renderAssets();invalidatePreviews()});
  bindMentionChipClicks();
  scheduleDraftSave();
}
function invalidatePreviews(){
 const had=!!state.videoPreviewToken;state.videoPreviewToken=null;state.replicaPreviewToken=null;
 $('#generateVideo').disabled=true;$('#generateReplica').disabled=true;
 if(had){const box=$('#videoPreviewBox');if(box)box.innerHTML='<div class="workspace-empty compact-preview-empty"><b>参数已变化，需要重新预览</b><span>上一次的模型、清晰度、时长及费用已作废；不会沿用旧令牌发起付费生成。</span></div>'}
}
function setReplicaReadyState(kind,title,detail){
 const box=$('#replicaReadyState');if(!box)return;
 const icon=kind==='ready'?'✓':kind==='loading'?'…':kind==='error'?'!':'○';
 box.className=`replica-ready-state-v34 ${kind||'idle'}`;
 box.innerHTML=`<div class="ready-icon-v34">${icon}</div><div><b>${esc(title||'等待准备')}</b><span>${esc(detail||'')}</span></div>`;
}
function invalidateReplicaPreparation(reason='输入已变化，请重新点击“分析并准备生成”。'){
 state.replicaPreviewToken=null;state.workflowConfirmed=false;
 const g=$('#generateReplica');if(g)g.disabled=true;
 const pb=$('#replicaPreviewBox');if(pb)pb.innerHTML='';
 setReplicaReadyState('idle','需要重新准备',reason);
}
function resetReplicaWorkflow(){state.workflowVersionId=null;state.workflowGroupId=null;state.workflowConfirmed=false;state.replicaPreviewToken=null;
 const we=$('#workflowEditor');if(we)we.value='';
 const wv=$('#workflowVersions');if(wv)wv.innerHTML='';
 const an=$('#anchorStrip');if(an)an.innerHTML='';
 ['#blueprintStrategy','#blueprintContracts','#blueprintRisks','#blueprintTimeline'].forEach(id=>{const e=$(id);if(e)e.innerHTML=''});
 const g=$('#generateReplica');if(g)g.disabled=true;
 const pb=$('#replicaPreviewBox');if(pb)pb.innerHTML='';
 setReplicaReadyState('idle','等待准备','上传参考视频并填写最终效果要求后，点击“分析并准备生成”。');
}
async function acceptUploadedAssets(scope,files){
 const a=await uploadFileList(files);
 if(scope==='video'){
   const fresh=a.filter(x=>!state.videoRefs.some(v=>v.id===x.id));if(state.videoRefs.length+fresh.length>50)throw new Error('参考素材最多 50 个');state.videoRefs.push(...fresh);renderAssets();invalidatePreviews();toast(`已添加 ${fresh.length} 个参考素材${fresh.length<a.length?'（重复素材已自动忽略）':''}`);return;
 }
 if(scope==='replica-video'){
   const x=a.find(v=>v.kind==='video');if(!x)throw new Error('这里需要视频文件');state.replicaVideo=x;renderAssets();resetReplicaWorkflow();toast(`参考视频已读取${x.duration?`，约 ${Number(x.duration).toFixed(2)} 秒`:''}`);return;
 }
 if(scope==='replica-product'){
   const imgs=a.filter(x=>x.kind==='image');if(!imgs.length)throw new Error('这里需要图片文件');const fresh=imgs.filter(x=>!state.replicaProducts.some(v=>v.id===x.id));state.replicaProducts.push(...fresh);renderAssets();invalidateReplicaPreparation('产品参考图已变化，请重新准备生成。');toast(`已添加 ${fresh.length} 张产品图${fresh.length<imgs.length?'（重复图片已自动忽略）':''}`);return;
 }
 if(scope==='replica-audio'){
   const audios=a.filter(x=>x.kind==='audio');if(!audios.length)throw new Error('这里需要音频文件');
   if(window.SeedanceAudioRef)window.SeedanceAudioRef.showUploaded('replica',audios[0]);
   else throw new Error('音频面板未初始化，请刷新网页');
   return;
 }
 if(scope==='image-source'){
   const imgs=a.filter(x=>x.kind==='image');if(!imgs.length)throw new Error('这里需要图片文件');state.imageSourceAssets=[imgs[0]];renderImageSourceList();toast('图生图起始图片已更新');return;
 }
}
function clipboardFiles(e){
 const out=[];const seen=new Set();const push=f=>{if(!f)return;const k=[f.name||'',f.size||0,f.type||'',f.lastModified||0].join('|');if(!seen.has(k)){seen.add(k);out.push(f)}};
 [...(e.clipboardData?.files||[])].forEach(push);
 [...(e.clipboardData?.items||[])].forEach(it=>{if(it.kind==='file')push(it.getAsFile())});
 return out;
}
function setupUploadZone(zoneId,scope){
 const zone=$(zoneId);if(!zone)return;
 ['dragenter','dragover'].forEach(ev=>zone.addEventListener(ev,e=>{e.preventDefault();e.stopPropagation();zone.classList.add('drag-active')}));
 ['dragleave','drop'].forEach(ev=>zone.addEventListener(ev,e=>{e.preventDefault();e.stopPropagation();zone.classList.remove('drag-active')}));
 zone.addEventListener('drop',async e=>{const files=[...(e.dataTransfer?.files||[])];if(!files.length)return toast('拖入内容里没有可读取的文件',true);try{await acceptUploadedAssets(scope,files)}catch(err){toast(err.message,true)}});
 zone.addEventListener('paste',async e=>{const files=clipboardFiles(e);if(!files.length)return;e.preventDefault();e.stopPropagation();try{await acceptUploadedAssets(scope,files)}catch(err){toast(err.message,true)}});
}

$('#videoRefs').onchange=async()=>{try{const a=await uploadFiles($('#videoRefs'));const fresh=a.filter(x=>!state.videoRefs.some(v=>v.id===x.id));if(state.videoRefs.length+fresh.length>50)throw new Error('参考素材最多 50 个');state.videoRefs.push(...fresh);renderAssets();invalidatePreviews();toast(`已上传 ${fresh.length} 个参考素材${fresh.length<a.length?'（重复素材已自动忽略）':''}`)}catch(e){toast(e.message,true)}};
$('#replicaVideo').onchange=async()=>{try{const a=await uploadFiles($('#replicaVideo'));const x=a.find(v=>v.kind==='video');if(!x)throw new Error('这里需要视频文件');state.replicaVideo=x;renderAssets();resetReplicaWorkflow();toast(`参考视频已读取${x.duration?`，约 ${Number(x.duration).toFixed(2)} 秒`:''}`)}catch(e){toast(e.message,true)}};
$('#replicaProducts').onchange=async()=>{try{const a=await uploadFiles($('#replicaProducts'));const imgs=a.filter(x=>x.kind==='image');const fresh=imgs.filter(x=>!state.replicaProducts.some(v=>v.id===x.id));state.replicaProducts.push(...fresh);renderAssets();invalidateReplicaPreparation('产品参考图已变化，请重新准备生成。');toast(`已上传 ${imgs.length} 张产品图`)}catch(e){toast(e.message,true)}};
$('#imageSourceInput')?.addEventListener('change',async()=>{try{const a=await uploadFiles($('#imageSourceInput'));const imgs=a.filter(x=>x.kind==='image');if(!imgs.length)throw new Error('这里需要图片文件');state.imageSourceAssets=[imgs[0]];renderImageSourceList();toast('图生图起始图片已上传')}catch(e){toast(e.message,true)}});
setupUploadZone('#videoRefsZone','video');setupUploadZone('#replicaVideoZone','replica-video');setupUploadZone('#replicaProductsZone','replica-product');setupUploadZone('#imageSourceZone','image-source');
// Page-level paste: when the user is not typing into a form field, paste clipboard media directly into the active workspace.
document.addEventListener('paste',async e=>{const tag=(e.target?.tagName||'').toLowerCase();if(['input','textarea','select'].includes(tag)||e.target?.isContentEditable)return;const files=clipboardFiles(e);if(!files.length)return;const active=$('.page.active')?.id;let scope=null;if(active==='page-video')scope='video';else if(active==='page-replica')scope=files.some(f=>(f.type||'').startsWith('audio/'))?'replica-audio':files.some(f=>(f.type||'').startsWith('video/'))?'replica-video':'replica-product';else if(active==='page-image')scope='image-source';if(!scope)return;e.preventDefault();try{await acceptUploadedAssets(scope,files)}catch(err){toast(err.message,true)}});

const MODE_RULES={
 auto:'像即梦那样的智能参考模式。系统根据素材类型和提示词自动判断是文生视频、图片参考、首帧 / 首尾帧、全能参考、视频编辑还是视频延长；你只需要上传素材并写清需求。',
 text:'纯文本生成。参考素材不会提交；比例和时长可自定义。',
 image_reference:'上传 1 张或多张图片作为主体 / 场景 / 风格参考；比例和时长可自定义。',
 first_frame:'第 1 张图片严格作为首帧。系统自动锁定比例为 adaptive（自适应），时长仍可设置。',
 first_last:'上传顺序第 1 张=首帧，第 2 张=尾帧。系统自动锁定比例为 adaptive（自适应）；建议两张图画幅一致。',
 reference:'图片 / 视频 / 音频均可作为全能参考；比例和时长可自定义。',
 edit:'至少上传 1 个待编辑视频。系统自动提交 ratio=adaptive、duration=-1，继承原视频画幅和时长；建议提示词明确写“修改/替换/删除/增加”。',
 extend:'至少上传 1 个原视频。系统自动锁定比例为 adaptive；请在提示词明确“向前延长 / 向后延长 / 续写”。'
};
function updateModeUi(){const m=$('#videoMode').value;const lockRatio=['first_frame','first_last','edit','extend'].includes(m);const lockDuration=m==='edit';$('#videoRatio').disabled=lockRatio;$('#videoDuration').disabled=lockDuration;if(lockRatio)$('#videoRatio').value='adaptive';else if($('#videoRatio').value==='adaptive')$('#videoRatio').value='9:16';$('#modeRule').textContent=MODE_RULES[m]||'';renderAssets();invalidatePreviews()}
$('#videoMode').onchange=()=>{updateModeUi();scheduleCostHud()};['videoPrompt','videoRatio','videoResolution','videoDuration'].forEach(id=>$('#'+id).addEventListener('input',()=>{renderAssets();invalidatePreviews();if(id==='videoResolution'||id==='videoDuration')scheduleCostHud()}));updateModeUi();

function previewHtml(p){
 const liveEst=state.costData?.estimate||{};
 const liveCostRow=/doubao-seedance-2-5/i.test(p.model||'')&&liveEst.configured
   ? `<tr><td>当前预计消耗</td><td>${liveEst.fallback_used?`≈ $${money(liveEst.cost_usd,2)}（¥${money(liveEst.cost_cny,2)}，备用公式）`:`≈ ${money(liveEst.cost,3)}（${Number(liveEst.estimated_tokens||0).toLocaleString()} Tokens × ${money(liveEst.price_per_m||liveEst.model_price?.price_per_m,3)}/1M）`}</td></tr>`
   : '<tr><td>当前预计消耗</td><td>暂无法估算</td></tr>';
const routed=p.router_decision;
 const routerRow=routed?`<tr><td>智能选择模式</td><td>${esc(routed.routing_mode)}</td></tr><tr><td>页面任务类型</td><td>${esc(routed.requested_task_type||'-')}</td></tr><tr><td>后端识别类型</td><td>${esc(routed.task_type)}</td></tr><tr><td>动作参考要求</td><td>${esc(routed.motion_fidelity)} · ${routed.approx_confirmed?'已确认允许近似':'未确认放宽'}</td></tr><tr><td>选择原因</td><td>${esc(routed.selection_reason)}</td></tr>${(routed.warnings||[]).map(w=>`<tr><td>约束提醒</td><td>${esc(w)}</td></tr>`).join('')}<tr><td>费用依据</td><td>预计总费用：${routed.quote.estimated_total_usd==null?'待验证':'$'+routed.quote.estimated_total_usd}；${routed.quote.reference_lower_bound_usd==null?'':('目录参考起价（非实际费用）：$'+routed.quote.reference_lower_bound_usd)}，请按真实分组及素材类型核验</td></tr>`:'';
const cost=p.estimated_cost_usd==null?'未配置真实单价（不是 0 美元）':`约 $${Number(p.estimated_cost_usd).toFixed(4)}`;const providerSort=p.provider?.sort||'-';const routeMap={model_suffix:'模型名后缀',provider_sort:'provider.sort',token_default:'Token 级默认'};const prefMap={success_rate:'成功率最高',speed:'速度最快',price:'价格最低'};const strategyRow=p.execution_strategy?`<tr><td>执行蓝图策略</td><td>${esc(p.execution_strategy)}</td></tr>`:'';const requestedRow=p.requested_mode?`<tr><td>用户选择模式</td><td>${esc(p.requested_mode)}</td></tr>`:'';const voiceBindingBlock=(p.audio_voice_bindings||[]).length?`<div class="voice-binding-review"><b>本次音色角色绑定</b>${p.audio_voice_bindings.map(x=>`<div>${esc(x.alias)} → ${esc(x.target)} <small>(${x.source==='explicit_at'?'@ 明确指定':'单人默认'})</small></div>`).join('')}</div>`:'';const bindingBlock=(p.reference_bindings||[]).length?`<div class="reference-bind-list">${p.reference_bindings.map(x=>`<div class="reference-bind-item"><div><b>${esc(x.alias||'-')}</b><small>${esc(x.provider_alias||'')} · ${esc(x.name||x.kind||'素材')}</small></div><div>${esc(x.role||'普通参考')}</div></div>`).join('')}</div>`:'';return `<table class="preview-table">${routerRow}${requestedRow}<tr><td>实际提交模式</td><td>${esc(p.mode||'-')}</td></tr>${strategyRow}<tr><td>图片生成调用</td><td>${p.calls?.image??0} 次</td></tr><tr><td>视频生成调用</td><td>${p.calls?.video??0} 次</td></tr><tr><td>路由控制</td><td>${esc(routeMap[p.route_control]||p.route_control||'-')}</td></tr><tr><td>路由目标</td><td>${esc(prefMap[p.route_preference]||(p.route_control==='token_default'?'由 Token 设置决定':'-'))}</td></tr><tr><td>实际 model 字段</td><td>${esc(p.model)}</td></tr><tr><td>成片音频模式</td><td>${esc((p.audio_reference_mode==='timbre'?'仅参考上传音频音色（模型新台词，不合成原音轨）':p.audio_reference_mode==='content'?'保留上传音频原内容与音色（本地合成）':({original:'参考视频原音频（本地合成）',uploaded:'使用已上传音频（本地合成）',model:'按提示词生成新音频',silent:'无声视频'})[p.audio_mode]||'兼容旧任务'))}</td></tr><tr><td>provider.sort</td><td>${esc(providerSort)}</td></tr><tr><td>时长</td><td>${esc(p.duration)}${typeof p.duration==='number'?'s':''}</td></tr>${p.billing_duration?`<tr><td>本地计费参考时长</td><td>${esc(p.billing_duration)}s</td></tr>`:''}<tr><td>最终导出清晰度 / 比例</td><td>${esc(p.resolution)} / ${esc(p.ratio)}</td></tr>${p.provider_resolution&&p.provider_resolution!==p.resolution?`<tr><td>实际付费生成规格</td><td>${esc(p.provider_resolution)}（完成后本地缩小至${esc(p.resolution)}，费用按${esc(p.provider_resolution)}档位）</td></tr>`:''}<tr><td>参考素材</td><td>${p.media_count??0} 个</td></tr><tr><td>输出格式</td><td>${esc(p.output_format||'mp4')}</td></tr><tr><td>创建接口</td><td>${esc(p.create_path||'-')}</td></tr>${liveCostRow}<tr><td>旧版费用字段</td><td>${cost}</td></tr></table>${bindingBlock}${voiceBindingBlock}<p class="tiny muted">${esc(p.pricing_note||'')}</p>${(p.warnings||[]).map(w=>`<p class="tiny warn">⚠ ${esc(w)}</p>`).join('')}`}

$('#previewVideo').onclick=async()=>{
 invalidatePreviews();updateVideoRouteStatus();
 try{const p=await post('/api/video/direct-preview',{prompt:$('#videoPrompt').value,video_mode:$('#videoMode').value,asset_ids:state.videoRefs.map(x=>x.id),audio_reference_mode:selectedAudioReferenceMode('video'),ratio:$('#videoRatio').value,resolution:$('#videoResolution').value,duration:+$('#videoDuration').value,video_model:selectedTaskModel('#videoModelChoice'),});matchResolutionSelect('#videoResolution',p.router_decision?.selected_model||p.model,{silent:true,requested:p.resolution});state.videoPreviewToken=p.preview_token;$('#videoPreviewBox').innerHTML=previewHtml(p);$('#generateVideo').disabled=false;setWorkspaceTab('video','preview')}
 catch(e){
   const model=selectedTaskModel('#videoModelChoice'),name=$('#videoModelChoice')?.selectedOptions?.[0]?.textContent?.trim()||model;
   const info=`<div class="workspace-empty compact-preview-empty"><b>本次费用预览未通过（没有生成视频）</b><span>${esc(e.message)}</span><span>当前选择模型：${esc(name)}｜音频：${esc(selectedAudioReferenceMode('video')||'按原有规则')}｜时长：${esc($('#videoDuration').value)}秒｜清晰度：${esc($('#videoResolution').value)}</span></div>`;
   $('#videoPreviewBox').innerHTML=info;$('#generateVideo').disabled=true;
setWorkspaceTab('video','preview');toast(e.message,true)
 }
};


function fmtElapsed(sec){sec=Math.max(0,Math.floor(Number(sec)||0));const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;return h?`${h}时 ${String(m).padStart(2,'0')}分 ${String(s).padStart(2,'0')}秒`:(m?`${m}分 ${String(s).padStart(2,'0')}秒`:`${s}秒`)}
function stageIcon(st){return ({done:'✓',active:'●',waiting:'○',failed:'!',unknown:'?',skipped:'—'})[st]||'○'}
function stageClass(st){return `stage-${st||'waiting'}`}
// V4.2.8.6: Link the historical prompt to its own persisted task ID.
// Never take the current editor text as the prompt for a previously generated video.
function savedVideoPrompt(task){
 const r=task?.request||{};
 const original=(r.original_instruction||r.user_prompt||r.user_instruction||r.instruction||'').trim();
 if(original)return {text:normalizePromptMentions(original),label:'本次生成时保存的原始提示词'};
 const fallback=String(r.prompt||'').trim();
 if(fallback)return {text:normalizePromptMentions(fallback),label:'旧任务未保存原始输入；以下为历史模型提交提示词'};
 return {text:'',label:'旧任务没有可恢复的提示词记录'};
}
function resultPromptPanel(task){
 const p=savedVideoPrompt(task);
 return `<details class="result-prompt-history" data-result-prompt><summary>本次视频的历史提示词 <span class="muted tiny">点击展开全文</span></summary><div class="result-prompt-subtitle">${esc(p.label)} · Task ID：${esc(task.id)}</div><div class="result-prompt-full">${esc(p.text||'此历史任务未保存文字提示词')}</div><div class="history-action-row"><button class="secondary" type="button" data-copy-result-prompt ${!p.text?'disabled':''}>复制完整提示词</button><button class="secondary" type="button" data-reuse-result-prompt>复用这次生成</button></div><div class="tiny muted">复用时还会尝试恢复历史素材和生成参数；点击复用不会自动产生付费请求。</div></details>`;
}
async function copySavedPrompt(text){
 if(!text)return toast('这个旧任务没有保存可复制的提示词',true);
 try{
  if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text)}
  else{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();if(!document.execCommand('copy'))throw Error('未获复制权限');ta.remove()}
  toast('完整历史提示词已复制');
 }catch(e){toast('复制失败：'+e.message,true)}
}
function taskRenderSignature(task){const p=task.progress||{},r=task.result||{},q=task.quality_report||{};return [task.id,task.status,p.current_key,task.provider_task_id||'',task.error||'',task.last_query_error||'',r.local_url||'',r.remote_url||'',r.download_error||'',r.audio_status||'',r.audio_error||'',q.created_at||'',(q.issues||[]).join(',')].join('|')}
function taskElapsedSeconds(task,now=Date.now()/1000){const start=Number(task.created_at)||0;if(!start)return 0;const ended=['completed','failed','cancelled','canceled','rejected'].includes(task.status);const end=ended?(Number(task.finished_at||task.completed_at||task.updated_at)||start):now;return Math.max(0,end-start)}
function updateTaskLiveFields(box,task){const p=task.progress||{};const elapsed=box.querySelector('[data-task-elapsed]');if(elapsed){const base=Number(task.created_at||0);elapsed.dataset.createdAt=String(base);elapsed.textContent=fmtElapsed(base?taskElapsedSeconds(task):p.elapsed_seconds||0)}const poll=box.querySelector('[data-task-polls]');if(poll)poll.textContent=String(p.query_count||task.query_count||0);const last=box.querySelector('[data-task-lastpoll]');if(last)last.textContent=p.last_polled_at?new Date(p.last_polled_at*1000).toLocaleTimeString():'尚未轮询';const ps=box.querySelector('[data-provider-status]');if(ps)ps.textContent=p.provider_status||statusLabel(task.status)}
function startTaskTicker(boxSel,task){if(!task?.id)return;const key=`${boxSel}:${task.id}`;if(['completed','failed','cancelled','canceled','rejected'].includes(task.status)){clearInterval(state.taskTickers[key]);delete state.taskTickers[key];return;}if(state.taskTickers[key])return;state.taskTickers[key]=setInterval(()=>{const box=$(boxSel);if(!box||box.dataset.taskId!==task.id){clearInterval(state.taskTickers[key]);delete state.taskTickers[key];return}const el=box.querySelector('[data-task-elapsed]');if(el){const created=Number(el.dataset.createdAt||task.created_at||0);if(created)el.textContent=fmtElapsed(Date.now()/1000-created)}},1000)}
function showTask(boxSel,task){const box=$(boxSel);if(!box||!task)return;const p=task.progress||{},result=task.result||{},req=task.request||{};const videoUrl=result.local_url||result.remote_url;const sig=taskRenderSignature(task);if(box.dataset.taskId!==task.id||box.dataset.taskSig!==sig){const wasOpen=box.dataset.taskId===task.id&&!!box.querySelector('[data-result-prompt][open]');const stages=(p.stages||[]).map((x,i)=>`<div class="task-stage ${stageClass(x.state)}"><div class="task-stage-dot">${stageIcon(x.state)}</div><div class="task-stage-main"><div class="task-stage-title">${esc(x.label)}${x.state==='active'?'<span class="stage-live">进行中</span>':''}</div>${x.detail?`<div class="task-stage-detail">${esc(x.detail)}</div>`:''}</div></div>`).join('');const stagePct=Math.max(0,Math.min(100,Number(p.stage_percent)||0));const route=req.route_control==='provider_sort'?`provider.sort=${req.provider?.sort||req.route_preference||'-'}`:(req.route_control==='token_default'?'Token 默认':(req.route_preference?`模型名后缀 · ${req.route_preference}`:'模型名后缀'));const q=task.quality_report||null;const qualityHtml=q?`<div class="task-quality-report"><div class="row between"><b>本地自动质检</b><span class="${q.pass?'ok':'warn'}">${q.pass?'基础检查通过':'建议人工复查'}</span></div><div class="quality-list">${(q.issues||[]).map(x=>`<span class="bad">问题：${esc(x)}</span>`).join('')}${(q.warnings||[]).map(x=>`<span class="warn">提醒：${esc(x)}</span>`).join('')}${!(q.issues||[]).length&&!(q.warnings||[]).length?'<span>未发现黑帧、异常静帧、明显画幅/时长异常。</span>':''}</div><small class="muted">${esc(q.note||'')}</small></div>`:'';box.innerHTML=`<div class="task task-progress-card"><div class="task-progress-top"><div><div class="task-kicker">当前任务</div><b class="task-current-step">${esc(p.current_label||statusLabel(task.status))}</b></div><span class="task-status-pill status-${esc(task.status)}">${esc(statusLabel(task.status))}</span></div><div class="task-progress-meta"><span>流程阶段 ${p.stage_done??0}/${p.stage_total??0}</span><span>${stagePct}%</span></div><div class="task-progress-track"><span style="width:${stagePct}%"></span></div><div class="tiny muted task-progress-note">${esc(p.note||'这里显示的是流程阶段进度，不代表模型内部渲染百分比。')}</div><div class="task-stage-list">${stages}</div><div class="task-runtime-grid"><div><span>本地任务 ID</span><b>${esc(task.id)}</b></div><div><span>平台 Task ID</span><b>${esc(task.provider_task_id||'等待平台返回')}</b></div><div><span>实际请求模型</span><b>${esc(task.actual_model||req._request_model||req.model||'-')}</b></div><div><span>路由</span><b>${esc(route)}</b></div><div><span>已耗时</span><b data-task-elapsed data-created-at="${Number(task.created_at||0)}">${fmtElapsed(p.elapsed_seconds||0)}</b></div><div><span>平台轮询</span><b><span data-task-polls>${p.query_count||task.query_count||0}</span> 次</b></div><div><span>最近轮询</span><b data-task-lastpoll>${p.last_polled_at?new Date(p.last_polled_at*1000).toLocaleTimeString():'尚未轮询'}</b></div><div><span>平台状态</span><b data-provider-status>${esc(p.provider_status||statusLabel(task.status))}</b></div></div>${task.error?`<div class="task-error-box"><b>失败原因</b><div>${esc(task.error)}</div>${task.status==='submission_unknown'?'<small>提交结果不确定时系统不会自动重试，避免重复扣费。请先检查 OpenLux 后台任务/账单。</small>':''}</div>`:''}${task.last_query_error?`<div class="task-warning-box"><b>最近一次状态查询失败</b><div>${esc(task.last_query_error)}</div><small>不会重新提交付费任务；后台会继续尝试查询。</small></div>`:''}${result.audio_error?`<div class="task-warning-box"><b>生成成功，但所选音轨处理失败</b><div>${esc(result.audio_error)}</div><small>原始视频仍保留；本地重新处理不调用付费视频接口。</small><div class="history-action-row"><button class="secondary" type="button" data-retry-task-audio="${esc(task.id)}">重新合成音轨（不扣费）</button>${result.unprocessed_local_url?`<a class="download-link secondary-link" target="_blank" href="${esc(result.unprocessed_local_url)}">查看未处理原视频</a>`:''}</div></div>`:''}${result.download_error?`<div class="task-warning-box"><b>平台已生成，但本地保存失败</b><div>${esc(result.download_error)}</div><small>不会重新生成视频。可使用“重新下载到本地”。</small><div class="history-action-row"><button class="secondary" type="button" data-retry-task-download="${esc(task.id)}">重新下载到本地</button></div></div>`:''}${videoUrl?`<div class="task-result"><div class="task-result-head"><b>生成结果</b><span class="ok">已返回</span></div><video controls src="${esc(videoUrl)}"></video><div class="row gap task-result-actions"><a class="download-link" href="${esc(videoUrl)}" target="_blank">打开 / 下载视频</a><button class="secondary" type="button" data-open-task-output>打开输出目录</button>${result.remote_url&&result.local_url?`<a class="download-link secondary-link" href="${esc(result.remote_url)}" target="_blank">查看平台原始结果</a>`:''}</div>${resultPromptPanel(task)}</div>`:''}${qualityHtml}${task.status==='failed'?'<div class="tiny muted">是否退费 / 退还额度以 OpenLux 后台账单为准，本地不会自动重试。</div>':''}</div>`;box.dataset.taskId=task.id;box.dataset.taskSig=sig;const promptEl=box.querySelector('[data-result-prompt]');if(promptEl&&wasOpen)promptEl.open=true;
 const cp=box.querySelector('[data-copy-result-prompt]');if(cp)cp.onclick=()=>copySavedPrompt(savedVideoPrompt(task).text);
 const rep=box.querySelector('[data-reuse-result-prompt]');if(rep)rep.onclick=()=>reuseHistoricalTask(task.id);
 const openBtn=box.querySelector('[data-open-task-output]');if(openBtn)openBtn.onclick=()=>openOutputFolder();const retryBtn=box.querySelector('[data-retry-task-download]');if(retryBtn)retryBtn.onclick=async()=>{retryBtn.disabled=true;try{const t=await post('/api/tasks/retry-download',{task_id:task.id});showTask(boxSel,t);toast('已重新下载并保存到本地')}catch(e){toast(e.message,true)}finally{retryBtn.disabled=false}};const retryAudio=box.querySelector('[data-retry-task-audio]');if(retryAudio)retryAudio.onclick=async()=>{retryAudio.disabled=true;try{const t=await post('/api/tasks/retry-audio',{task_id:task.id});showTask(boxSel,t);toast('已使用本地原视频重新处理音轨，不产生生成费用')}catch(e){toast(e.message,true)}finally{retryAudio.disabled=false}};const group=boxSel.includes('replica')?'replica':'video';taskTabState(group,task);startTaskTicker(boxSel,task)}updateTaskLiveFields(box,task)}
function pollTask(id,boxSel){if(!id)return;const key=`${boxSel}:${id}`;if(state.taskPollers[key])return;state.taskPollers[key]=true;const step=async()=>{const box=$(boxSel);if(box?.dataset.taskId&&box.dataset.taskId!==id){delete state.taskPollers[key];return}try{const t=await api(`/api/tasks/${encodeURIComponent(id)}`);const current=$(boxSel);if(current?.dataset.taskId&&current.dataset.taskId!==id){delete state.taskPollers[key];return}showTask(boxSel,t);if(['pending','running','result_pending'].includes(t.status)||t.quality_pending){state.taskPollers[key]=setTimeout(step,t.quality_pending?2200:5000)}else{delete state.taskPollers[key];refreshStatus();refreshHistory();refreshTaskCenter(true);syncAccountBalanceBurst()}}catch(e){toast(e.message,true);state.taskPollers[key]=setTimeout(step,8000)}};step()}
function showSubmittingTask(boxSel,kind='视频'){const box=$(boxSel);if(!box)return;box.dataset.taskId='';box.dataset.taskSig='';box.innerHTML=`<div class="task task-progress-card submitting-card"><div class="task-progress-top"><div><div class="task-kicker">正在建立任务</div><b class="task-current-step">准备公网素材并提交 ${esc(kind)}任务</b></div><span class="task-status-pill status-running">处理中</span></div><div class="task-progress-track indeterminate"><span></span></div><div class="task-stage-list"><div class="task-stage stage-active"><div class="task-stage-dot">●</div><div class="task-stage-main"><div class="task-stage-title">准备参考素材 / OSS 临时链接<span class="stage-live">进行中</span></div><div class="task-stage-detail">完成后才会提交付费视频接口；如果前置检查失败，本次不会产生视频生成调用。</div></div></div><div class="task-stage stage-waiting"><div class="task-stage-dot">○</div><div class="task-stage-main"><div class="task-stage-title">提交 OpenLux / Seedance</div></div></div></div></div>`}
function taskWorkspace(task){const r=task?.request||{};if(r.mode==='hypit')return 'hypit';return task?.workspace==='replica'||r.mode==='replica'||r.workflow_version_id?'replica':'direct'}
function taskCenterTypeLabel(task){return taskWorkspace(task)==='hypit'?'官方 Hypit':taskWorkspace(task)==='replica'?'爆款复刻':'视频创作'}
function taskCenterTitle(task){const r=task?.request||{};const type=taskCenterTypeLabel(task);const sec=Number(r.duration||r.billing_duration||0);const ratio=r.ratio||'';return `${type}${sec?` · ${sec}s`:''}${ratio?` · ${ratio}`:''}`}
function taskCenterStatusClass(status){return ({pending:'waiting',running:'running',result_pending:'sync',completed:'done',failed:'failed',submission_unknown:'failed'})[status]||'waiting'}
function updateTaskCenterCapacity(active,limit){active=Math.max(0,Number(active)||0);limit=Math.max(1,Number(limit)||10);const badge=$('#taskCenterBadge'),usage=$('#taskCenterUsage'),bar=$('#taskCenterCapacityBar');if(badge){badge.textContent=`${active}/${limit}`;badge.classList.toggle('full',active>=limit)}if(usage)usage.textContent=`${active}/${limit}`;if(bar)bar.style.width=`${Math.min(100,active/limit*100)}%`;}
function setTaskCenterUnread(n){state.taskCenterUnread=Math.max(0,Number(n)||0);const el=$('#taskCenterUnread');if(!el)return;el.textContent=String(state.taskCenterUnread);el.classList.toggle('hidden',!state.taskCenterUnread)}
function openTaskCenter(){refreshTaskHealth();const d=$('#taskCenterDrawer'),b=$('#taskCenterBackdrop');d?.classList.add('open');d?.setAttribute('aria-hidden','false');b?.classList.remove('hidden');setTaskCenterUnread(0);refreshTaskCenter(false)}
function closeTaskCenter(){const d=$('#taskCenterDrawer'),b=$('#taskCenterBackdrop');d?.classList.remove('open');d?.setAttribute('aria-hidden','true');b?.classList.add('hidden')}
$('#taskCenterNav')?.addEventListener('click',openTaskCenter);$('#closeTaskCenter')?.addEventListener('click',closeTaskCenter);$('#taskCenterBackdrop')?.addEventListener('click',closeTaskCenter);document.addEventListener('keydown',e=>{if(e.key==='Escape')closeTaskCenter()});
function globalTaskCard(task,recent=false){const p=task.progress||{},pct=Math.max(0,Math.min(100,Number(p.stage_percent)||0)),type=taskCenterTypeLabel(task),st=taskCenterStatusClass(task.status),result=task.result||{};const elapsed=fmtElapsed(taskElapsedSeconds(task));const platform=task.provider_task_id?esc(task.provider_task_id):esc(task.id)+'（本地编号）';const resultHint=result.local_url||result.remote_url?'<span class="global-result-ready">结果已就绪</span>':'';return `<article class="global-task-card ${recent?'is-recent':''}" data-global-task-card="${esc(task.id)}"><div class="global-task-top"><span class="global-task-type ${taskWorkspace(task)==='replica'?'replica':''}">${esc(type)}</span><span class="global-task-status ${st}">${esc(statusLabel(task.status))}</span></div><b class="global-task-title">${esc(taskCenterTitle(task))}</b><div class="global-task-progress"><span style="width:${pct}%"></span></div><div class="global-task-progress-meta"><span>${esc(p.current_label||statusLabel(task.status))}</span><b>${pct}%</b></div><div class="global-task-meta"><span>已耗时 ${esc(elapsed)}</span><span>阶段 ${p.stage_done??0}/${p.stage_total??0}</span></div><div class="global-task-id">Task ID：${platform}</div>${task.error?`<div class="global-task-error">${esc(task.error).slice(0,180)}</div>`:''}<details class="history-prompt-details"><summary>查看本次提示词</summary><div class="history-prompt-content">${esc((task.request||{}).user_prompt||(task.request||{}).original_instruction||(task.request||{}).prompt||(task.request||{}).instruction||(task.request||{}).user_instruction||'此任务未保存文字提示词')}</div></details><div class="global-task-actions">${resultHint}<button type="button" class="secondary" data-copy-global-task="${esc(task.id)}">复制提示词</button><button type="button" class="secondary" data-reuse-global-task="${esc(task.id)}">复用生成</button><button type="button" class="secondary" data-focus-global-task="${esc(task.id)}">${recent?'查看结果':'查看详情'}</button></div></article>`}
function renderTaskCenter(data){const active=data.active||[],recent=data.recent||[],limit=Number(data.limit||10),count=Number(data.active_count||active.length);updateTaskCenterCapacity(count,limit);const br=$('#taskCenterBreakdown');if(br)br.textContent=`视频创作 ${data.direct_active||0} · 旧版任务 ${data.replica_active||0} · Hypit ${data.hypit_active||0} · 空闲 ${data.free_slots??Math.max(0,limit-count)}`;const al=$('#taskCenterActiveLabel');if(al)al.textContent=`${count} 个任务`;const activeHtml=active.length?active.map(x=>globalTaskCard(x,false)).join(''):'<div class="workspace-empty compact"><b>当前没有运行中的视频任务</b><span>视频制作任务开始后会出现在这里。</span></div>';const a=$('#taskCenterActive');if(a)a.innerHTML=activeHtml;const r=$('#taskCenterRecent');if(r)r.innerHTML=recent.length?recent.map(x=>globalTaskCard(x,true)).join(''):'<div class="workspace-empty compact"><b>暂无最近结束任务</b><span>已完成或失败的任务会短暂保留在这里。</span></div>';$$('[data-task-center-inline-usage]').forEach(x=>x.textContent=`${count}/${limit}`);$$('[data-global-task-inline]').forEach(x=>x.innerHTML=activeHtml);[...active,...recent].forEach(x=>state.globalTasks.set(x.id,x));$$('[data-focus-global-task]').forEach(b=>b.onclick=()=>focusGlobalTask(b.dataset.focusGlobalTask));$$('[data-copy-global-task]').forEach(b=>b.onclick=()=>{const t=state.globalTasks.get(b.dataset.copyGlobalTask);return copySavedPrompt(savedVideoPrompt(t).text)});$$('[data-reuse-global-task]').forEach(b=>b.onclick=()=>reuseHistoricalTask(b.dataset.reuseGlobalTask))}
async function reuseHistoricalTask(id){
 try{
  const task=await api('/api/tasks/'+encodeURIComponent(id));const req=task.request||{};
  const all=await api('/api/assets');const assetMap=Object.fromEntries((all||[]).map(a=>[a.id,a]));
  const replica=taskWorkspace(task)==='replica';
  if(replica){
   const ids=req.product_asset_ids||req.product_ids||(req.media||[]).map(x=>x.asset_id).filter(x=>assetMap[x]?.kind!=='video')||[];
   const refId=req.reference_asset_id||req.video_id||(req.media||[]).find(x=>x.role==='reference_video'||assetMap[x.asset_id]?.kind==='video')?.asset_id;const vid=assetMap[refId];
   const missing=[refId,...ids].filter(Boolean).filter(x=>!assetMap[x]);
   if(missing.length){toast('部分历史素材已失效（'+missing.length+'个）。请在本地素材库补齐后再生成。',true)}
   state.replicaVideo=vid||null;
   state.replicaProducts=ids.map(x=>assetMap[x]).filter(x=>x?.kind==='image');
   state.replicaAudios=ids.map(x=>assetMap[x]).filter(x=>x?.kind==='audio');
   $('#replicaInstruction').value=normalizePromptMentions(req.original_instruction||req.instruction||req.user_instruction||req.prompt||'');
   if(req.resolution)$('#replicaResolution').value=req.resolution;
   const repAudioChoice=document.querySelector(`input[name="replicaAudioUse"][value="${req.audio_reference_mode||'timbre'}"]`);if(repAudioChoice)repAudioChoice.checked=true;
   const model=req.requested_model||req.model||'';
   if([...$('#replicaModelChoice').options].some(x=>x.value===model))$('#replicaModelChoice').value=model;
   resetReplicaWorkflow();renderAssets();activatePage('replica');
   toast('已恢复历史复刻资料。需重新执行分析及预检后才能提交新任务。');
  }else{
   const ids=req.asset_ids||((req.media||[]).map(x=>x.asset_id).filter(Boolean))||[];
   const missing=ids.filter(x=>!assetMap[x]);
   state.videoRefs=ids.map(x=>assetMap[x]).filter(Boolean);
   $('#videoPrompt').value=normalizePromptMentions(req.user_prompt||req.prompt||req.instruction||'');
   for(const [field,value] of [['videoMode',req.video_mode||req.mode],['videoRatio',req.ratio],['videoResolution',req.resolution],['videoDuration',req.duration]]){
    const el=$('#'+field);if(el&&value!==undefined&&value!==null)el.value=value;
   }
   const model=req.requested_model||req.model||'';
   if([...$('#videoModelChoice').options].some(x=>x.value===model))$('#videoModelChoice').value=model;
   const videoAudioChoice=document.querySelector(`input[name="videoAudioUse"][value="${req.audio_reference_mode||'timbre'}"]`);if(videoAudioChoice)videoAudioChoice.checked=true;
   updateModeUi();renderAssets();invalidatePreviews();activatePage('video');
   if(missing.length)toast('已恢复提示词和可用素材；'+missing.length+'个历史素材不在本机，需重新添加。',true);
   else toast('已恢复历史生成配置；修改后请先点击预览，再确认生成。');
  }
  scheduleDraftSave();closeTaskCenter();window.scrollTo({top:0,behavior:'smooth'});
 }catch(e){toast('复用任务失败：'+e.message,true)}
}
async function focusGlobalTask(id){try{const t=await api(`/api/tasks/${encodeURIComponent(id)}`);state.globalTasks.set(t.id,t);if(taskWorkspace(t)==='hypit'){await window.HypitStudio.open(t.request.project_id);closeTaskCenter();return}const rep=taskWorkspace(t)==='replica';const nav=$(`.nav[data-page="${rep?'replica':'video'}"]`);nav?.click();const boxSel=rep?'#replicaTaskBox':'#videoTaskBox';setWorkspaceTab(rep?'replica':'video','task');showTask(boxSel,t);if(['pending','running','result_pending'].includes(t.status)||t.quality_pending)pollTask(t.id,boxSel);closeTaskCenter()}catch(e){toast(e.message,true)}}
async function refreshTaskHealth(){
 const box=$('#taskHealthRows');if(!box)return;
 try{const r=await api('/api/task-health/overview');const tasks=r.tasks||[];
  const active=tasks.filter(t=>['running','pending','result_pending','submission_unknown','needs_review'].includes(t.status)||t.stalled).slice(0,8);
  box.innerHTML=active.length?active.map(t=>`<div class="task-health-row"><div class="task-health-top"><b>${esc(({product:'商品 GPT 理解',script:'TikTok 带货脚本',image:'图片生成',video:'视频制作'})[t.kind]||t.kind)}</b><span>${esc(statusLabel(t.status))}</span></div><div>${esc(t.phase)}</div><small>任务 ${esc(t.id)} · 模型 ${esc(t.model||'-')} · 耗时 ${fmtElapsed(t.elapsed_seconds||0)} · 最近更新 ${fmtElapsed(t.since_update_seconds||0)} 前</small>${t.stalled?'<div class="warn">该环节已超过预期时间，需核实原任务</div>':''}${t.error?`<div class="warn">${esc(t.error)}</div>`:''}<small>${esc(t.suggested_action||'')}</small>${t.kind==='product'&&t.status==='submission_unknown'?`<button type="button" class="secondary" data-reconcile-product="${esc(t.id)}">核对账单并解除原任务阻塞（不扣费）</button>`:''}</div>`).join(''):'<div class="workspace-empty compact">暂无等待或异常任务</div>';
  $$('[data-reconcile-product]').forEach(b=>b.onclick=async()=>{
    const taskId=b.dataset.reconcileProduct;
    if(!confirm('这条 GPT 请求曾超时，系统无法证明远端是否扣费。\n请先核对 OpenLux 账单，之后再继续；此操作不调用 GPT。'))return;
    const answer=prompt('在 OpenLux 账单中确认本任务是否扣费？输入 1 = 未发现扣费，2 = 已发生扣费。其他输入取消。','');
    if(answer!=='1'&&answer!=='2')return;
    const phrase=prompt('最后确认：请输入 已核对OpenLux账单 ，本操作仅标记账单核对完成，不会恢复 GPT 原结果。','');
    if(phrase!=='已核对OpenLux账单'){toast('未完成确认，不做任何修改',true);return}
    try{const out=await post('/api/task-health/product/reconcile',{task_id:taskId,billing_result:answer==='1'?'no_charge':'charged',confirmed:true,confirmation:phrase});toast(out.notice);await refreshTaskHealth();if(window.ProductMemory)window.ProductMemory.load()}
    catch(e){toast('任务核对失败：'+e.message,true)}
  });
  $('#taskHealthRefresh')?.setAttribute('title',`共${tasks.length}个记录，${r.attention_count||0}个需关注`);
 }catch(e){box.textContent='状态读取失败：'+e.message}
}
$('#taskHealthRefresh')?.addEventListener('click',refreshTaskHealth);
$('#taskHealthCopy')?.addEventListener('click',async()=>{
 try{const d=await api('/api/task-health/diagnostics');const text=JSON.stringify(d,null,2);await navigator.clipboard.writeText(text);toast('脱敏诊断报告已复制，没有发送付费请求')}catch(e){toast('复制失败：'+e.message,true)}
});
async function refreshTaskCenter(notify=true){refreshTaskHealth();try{const data=await api('/api/tasks/global');const all=[...(data.active||[]),...(data.recent||[])];if(state.taskCenterInitialized&&notify){for(const t of all){const prev=state.globalTaskStatuses.get(t.id);if(prev&&['pending','running','result_pending'].includes(prev)&&['completed','failed','submission_unknown'].includes(t.status)){setTaskCenterUnread(state.taskCenterUnread+1);toast(`${taskCenterTypeLabel(t)}任务${t.status==='completed'?'已完成':'需要处理'}：${taskCenterTitle(t)}`,t.status!=='completed')}}}state.globalTaskStatuses=new Map(all.map(x=>[x.id,x.status]));state.taskCenterInitialized=true;renderTaskCenter(data)}catch(e){console.warn('refresh task center failed',e)}}
function restoreTaskSlot(task,boxSel){if(!task)return;showTask(boxSel,task);const group=boxSel.includes('replica')?'replica':'video';setWorkspaceTab(group,'task');if(['pending','running','result_pending'].includes(task.status)||task.quality_pending)pollTask(task.id,boxSel)}
async function restorePersistedTasks(){try{const r=await api('/api/tasks/resume');const slots=r.slots||{};restoreTaskSlot(slots.direct,'#videoTaskBox');restoreTaskSlot(slots.replica,'#replicaTaskBox');state.restoredTasks=true;const active=r.active||[];if(active.length){const directN=active.filter(x=>taskWorkspace(x)==='direct').length,replicaN=active.filter(x=>taskWorkspace(x)==='replica').length;console.info(`Seedance Studio restored ${active.length} running task(s): direct=${directN}, replica=${replicaN}`)}}catch(e){console.warn('restore persisted tasks failed',e)}}
$('#generateVideo').onclick=async()=>{
 if(!state.videoPreviewToken||videoSubmitInFlight)return;
 videoSubmitInFlight=true;
 const token=state.videoPreviewToken;
 $('#generateVideo').disabled=true;
 setWorkspaceTab('video','task');
 showSubmittingTask('#videoTaskBox','视频');
 try{
   const r=await post('/api/video/direct-generate',{preview_token:token});
   const t=r.task;
   showTask('#videoTaskBox',t);
   pollTask(t.id,'#videoTaskBox');
   refreshTaskCenter(false);
   if(!r.reused)setTimeout(()=>refreshCostHud(true),1800);
   toast(r.reused?'重复提交已拦截，正在显示原任务':`已提交 1 次视频任务｜${r.actual_request_model||''}`);
 }catch(e){
   toast(e.message,true);
   if(e.payload?.task)showTask('#videoTaskBox',e.payload.task);
 }finally{
   if(state.videoPreviewToken===token)state.videoPreviewToken=null;
   videoSubmitInFlight=false;
 }
}


function renderExecutionBlueprint(wf){
 const bp=wf?.execution_blueprint||{};const sp=bp.source_profile||{},gs=bp.generation_strategy||{};
 const hero=$('#blueprintHero');if(hero){const goal=bp.goal||wf?.instruction||'尚未填写最终效果要求';hero.className='blueprint-hero';hero.innerHTML=`<div><span class="blueprint-label">最终效果目标</span><b>${esc(goal)}</b></div><div class="blueprint-score"><span>证据完整度</span><strong>${Math.round(Number(sp.evidence_score||wf?.analysis?.evidence_score||0)*100)}%</strong></div>`;}
 const strat=$('#blueprintStrategy');if(strat)strat.innerHTML=`<div class="blueprint-box-title">生成策略</div><b>${esc(gs.label||'整条参考复刻')}</b><p>${esc(gs.reason||'优先以单次完整视频生成保持连续性。')}</p><div class="mini-metrics"><span>视频创建调用 <b>${esc(gs.paid_video_create_calls??1)} 次</b></span><span>真实切镜 <b>${esc(sp.hard_cut_count??0)}</b></span><span>分析节拍 <b>${esc(sp.beat_count??0)}</b></span></div>`;
 const contracts=$('#blueprintContracts');if(contracts){const pre=(bp.preserve_contract||[]).slice(0,5).map(x=>`<li>${esc(x)}</li>`).join('');const rep=(bp.replacement_contract||[]).slice(0,4).map(x=>`<li>${esc(x)}</li>`).join('');contracts.innerHTML=`<div class="blueprint-box-title">执行约束</div><div class="contract-columns"><div><span>尽量保留</span><ul>${pre||'<li>等待蓝图</li>'}</ul></div><div><span>按提示词替换</span><ul>${rep||'<li>等待蓝图</li>'}</ul></div></div>`;}
 const risks=$('#blueprintRisks');if(risks){const arr=bp.risk_flags||[];risks.innerHTML=`<div class="blueprint-box-title">风险检查</div>${arr.map((x,i)=>`<div class="risk-line ${i===0&&arr.length===1&&String(x).includes('没有检测到')?'ok-line':''}"><span>${i===0&&arr.length===1&&String(x).includes('没有检测到')?'✓':'!'}</span><p>${esc(x)}</p></div>`).join('')||'<div class="risk-line ok-line"><span>✓</span><p>等待蓝图分析。</p></div>'}`;}
 const tl=$('#blueprintTimeline');if(tl){const rows=bp.timeline||[];tl.innerHTML=rows.map(x=>`<div class="timeline-card"><div class="timeline-index">${String(x.index||0).padStart(2,'0')}</div><div class="timeline-main"><div class="timeline-top"><b>${Number(x.start||0).toFixed(2)}–${Number(x.end||0).toFixed(2)}s</b><span>${esc(x.role||'结构段')}</span></div>${x.speech_text?`<p>口播：${esc(x.speech_text)}</p>`:''}${x.ocr_text?`<p>画面文字：${esc(x.ocr_text)}</p>`:''}<small>${esc(x.continuity_rule||'保持与下一段自然连续。')}</small></div></div>`).join('')||'<div class="workspace-empty compact"><b>还没有时间轴</b><span>解析参考视频后自动生成。</span></div>';}
}
function renderAnchors(wf){const a=wf?.anchors||[];$('#anchorStrip').innerHTML=a.map(x=>`<div class="anchor">${x.url?`<img src="${x.url}" alt="视觉锚点">`:''}<span>${esc(x.label)}<br>${Number(x.time||0).toFixed(2)}s${x.confidence!=null?` · ${Math.round(x.confidence*100)}%`:''}</span></div>`).join('')||'<div class="muted tiny">没有足够本地视觉证据；不建议直接进入付费复刻。</div>';renderAnalysisSummary(wf)}
function renderAnalysisSummary(wf){const a=wf?.analysis||{},g=a.paid_test_gate||{},sd=a.scene_detection||{},sg=a.segmentation||{},tr=a.transcript||{};const cuts=sd.cut_count??0;const beats=sg.beat_count??(wf?.segments?.length||0);const shotText=cuts===0&&sd.ok?'0（连续单镜头）':String(cuts);const dur=Number(wf?.source?.duration||wf?.duration||0);const cov=Number(a.word_anchor_coverage||0);const collapsed=dur>7&&cuts===0&&beats<=1&&(tr.used||cov>0);const reason=collapsed?'检测到旧/异常蓝图：长视频已有逐词证据，但仍只有 1 个分析节拍。请重新点击“解析爆款并建立执行蓝图”，系统会自动重建多个单镜头内节拍。':(g.reason||'');$('#analysisSummary').innerHTML=`<b>本地执行蓝图分析</b>｜真实镜头切换：${shotText}｜分析节拍：${beats}｜证据完整度：${Math.round((a.evidence_score||0)*100)}%｜逐词锚点覆盖：${Math.round(cov*100)}%｜逐词引擎：${tr.used?esc(tr.engine||'已使用'):(tr.available?'可用但未产出':'未安装')}<br><span class="${(!collapsed&&g.pass)?'ok':'warn'}">${(!collapsed&&g.pass)?'✓ 可进入人工确认':'⚠ 暂不建议付费测试'}：${esc(reason)}</span>`}
async function refreshWorkflowVersions(){if(!state.workflowGroupId){$('#workflowVersions').innerHTML='';return}try{const rows=await api(`/api/workflows?group_id=${encodeURIComponent(state.workflowGroupId)}`);$('#workflowVersions').innerHTML=rows.map(r=>`<div class="wf-card"><b class="${r.version_id===state.workflowVersionId?'current':''}">${esc(r.version_id)}</b><div class="tiny muted">${fmtTime(r.created_at)}${r.confirmed?' · 已确认':''}</div><button class="secondary" data-load-wf="${esc(r.version_id)}">加载此版 / 从此版分叉</button></div>`).join('');$$('[data-load-wf]').forEach(b=>b.onclick=()=>{const r=rows.find(x=>x.version_id===b.dataset.loadWf);if(!r)return;state.workflowVersionId=r.version_id;state.workflowConfirmed=!!r.confirmed;$('#workflowEditor').value=JSON.stringify(r.workflow,null,2);renderAnchors(r.workflow);renderExecutionBlueprint(r.workflow);$('#saveWorkflow').disabled=false;$('#confirmWorkflow').disabled=false;$('#previewReplica').disabled=!r.confirmed;$('#generateReplica').disabled=true;refreshWorkflowVersions();setWorkspaceTab('replica','analysis');scheduleDraftSave();toast('已加载该蓝图版本')})}catch(e){toast(e.message,true)}}
$('#replicaInstruction')?.addEventListener('input',()=>invalidateReplicaPreparation('最终效果要求已修改，请重新准备生成。'));
$('#replicaResolution')?.addEventListener('change',()=>{invalidateReplicaPreparation('复刻清晰度已更改，需重新预检费用。');scheduleDraftSave()});

$('#analyzeReplica').onclick=async()=>{
 if(!state.replicaVideo)return toast('请先上传参考爆款视频',true);
 const btn=$('#analyzeReplica');btn.disabled=true;
 setWorkspaceTab('replica','ready');
 setReplicaReadyState('loading','正在分析并准备生成','正在自动处理视频结构、动作节拍、约束和最终生成策略。');
 const pb=$('#replicaPreviewBox');if(pb)pb.innerHTML='';
 $('#generateReplica').disabled=true;
 try{
   const r=await post('/api/workflow/analyze',{
     reference_asset_id:state.replicaVideo.id,
     product_asset_ids:[...state.replicaProducts,...state.replicaAudios].map(x=>x.id),
     instruction:$('#replicaInstruction').value,
     reference_aliases:{}
   });
   state.workflowVersionId=r.version_id;
   state.workflowGroupId=r.group_id;
   scheduleDraftSave();
   $('#workflowEditor').value=JSON.stringify(r.workflow,null,2);
   renderAnchors(r.workflow);
   renderExecutionBlueprint(r.workflow);

   // 后台自动确认内部蓝图：用户不再需要操作这一层。
   await post('/api/workflow/confirm',{version_id:state.workflowVersionId});
   state.workflowConfirmed=true;
   scheduleDraftSave();

   // 自动生成最终付费调用预览，但此时仍未调用 Seedance 创建任务。
   const p=await post('/api/video/preview',{workflow_version_id:state.workflowVersionId,video_model:selectedTaskModel('#replicaModelChoice'),audio_reference_mode:selectedAudioReferenceMode('replica'),resolution:$('#replicaResolution').value});
   matchResolutionSelect('#replicaResolution',p.router_decision?.selected_model||p.model,{silent:true,requested:p.resolution});state.replicaPreviewToken=p.preview_token;
   $('#replicaPreviewBox').innerHTML=previewHtml(p);
   $('#generateReplica').disabled=false;

   const warnCount=(p.warnings||[]).length;
   const detail=`准备完成：预计视频生成调用 ${p.calls?.video??1} 次，${p.duration??'-'}s，${p.resolution||'-'} / ${p.ratio||'-'}${warnCount?`；有 ${warnCount} 条提示请查看下方预览。`:''}`;
   setReplicaReadyState('ready','准备完成，可以生成',detail);
   toast('准备完成。本步骤没有触发 Seedance 付费视频创建。');
 }catch(e){
   state.replicaPreviewToken=null;state.workflowConfirmed=false;
   $('#generateReplica').disabled=true;
   setReplicaReadyState('error','准备失败',e.message||'请检查输入后重试。');
   toast(e.message,true);
 }finally{
   btn.disabled=false;
 }
};

// 兼容保留：隐藏按钮不参与主流程。
$('#workflowEditor')?.addEventListener('input',()=>invalidateReplicaPreparation('内部工作流已变化，请重新准备生成。'));
$('#saveWorkflow').onclick=async()=>{};
$('#confirmWorkflow').onclick=async()=>{};
$('#previewReplica').onclick=async()=>{};

$('#generateReplica').onclick=async()=>{
 if(!state.replicaPreviewToken||replicaSubmitInFlight)return;
 replicaSubmitInFlight=true;
 const token=state.replicaPreviewToken;
 $('#generateReplica').disabled=true;
 setWorkspaceTab('replica','task');
 showSubmittingTask('#replicaTaskBox','复刻视频');
 try{
   const r=await post('/api/video/generate',{preview_token:token});
   const t=r.task;
   showTask('#replicaTaskBox',t);
   pollTask(t.id,'#replicaTaskBox');
   refreshTaskCenter(false);
   if(!r.reused)setTimeout(()=>refreshCostHud(true),1800);
   toast(r.reused?'重复提交已拦截，正在显示原任务':`已提交 1 次复刻视频任务｜${r.actual_request_model||''}`);
 }catch(e){
   toast(e.message,true);
   if(e.payload?.task)showTask('#replicaTaskBox',e.payload.task);
 }finally{
   if(state.replicaPreviewToken===token)state.replicaPreviewToken=null;
   replicaSubmitInFlight=false;
 }
}

function setImageMode(mode){state.imageMode=mode==='image'?'image':'text';$$('[data-image-mode]').forEach(b=>b.classList.toggle('active',b.dataset.imageMode===state.imageMode));$('#imageSourceWrap').classList.toggle('hidden',state.imageMode!=='image');const hint=$('#imageModeHint');if(hint)hint.innerHTML=state.imageMode==='image'?`当前模式：<b>图生图</b>。请上传 1 张起始图片，再写本轮修改要求。`:`当前模式：<b>文生图</b>。直接写完整提示词即可；不需要上传起始图片。`;scheduleDraftSave()}
function renderImageSourceList(){const box=$('#imageSourceList');if(!box)return;const recs=buildAliasRecords(state.imageSourceAssets,'image');box.innerHTML=recs.map(a=>`<div class="asset-card">${assetPreview(a)}<div class="asset-meta"><button type="button" class="alias-pill mention-chip" data-mention-scope="image" data-mention-insert="${esc(a.__alias)}" title="点击插入到提示词">${esc(a.__alias)}</button></div><small>${esc(a.original_name)}</small><button class="remove" data-remove-image-source="${esc(a.id)}">×</button></div>`).join('')||'<div class="muted tiny">还没有图生图起始图片。</div>';$$('[data-remove-image-source]').forEach(b=>b.onclick=()=>{const before=mentionScopeAssets('image');const rid=b.dataset.removeImageSource;state.imageSourceAssets=state.imageSourceAssets.filter(x=>x.id!==rid);rewriteAliasesAfterAssetChange('image',before,mentionScopeAssets('image'),[rid]);renderImageSourceList()});bindMentionChipClicks();scheduleDraftSave()}
$$('[data-image-mode]').forEach(b=>b.onclick=()=>setImageMode(b.dataset.imageMode));
$('#deepModeBtn').onclick=()=>{if(!state.imageVersions.length&&!state.deepMode){toast('请先生成第一张图片，再使用深度生成',true);return}state.deepMode=!state.deepMode;$('#deepModeBtn').classList.toggle('active',state.deepMode);$('#deepLevelWrap').classList.toggle('hidden',!state.deepMode);$('#deepSourceBox').classList.toggle('hidden',!state.deepMode);$('#changeRatioWrap').classList.toggle('hidden',!state.deepMode);if(!state.deepMode)$('#changeImageRatio').checked=false;if(state.deepMode&&!state.deepParentVersionId&&state.imageVersions[0])state.deepParentVersionId=state.imageVersions[0].version_id;renderDeepSource();scheduleDraftSave()};
function renderDeepSource(){if(!state.deepMode)return;const v=state.imageVersions.find(x=>x.version_id===state.deepParentVersionId);$('#deepSourceBox').innerHTML=v?`当前基于版本：<b>${esc(v.version_id)}</b><br>本轮会生成 1 张新图片，不覆盖父版本。`:'请选择一个历史版本作为修改起点。'}
function setCurrentImage(asset){state.currentImageAsset=asset||null;$('#addImageToVideo').disabled=!asset;$('#addImageToReplica').disabled=!asset;const useBtn=$('#useCurrentAsImageSource');if(useBtn)useBtn.disabled=!asset;if(asset)$('#imageResult').innerHTML=`<img src="${asset.url}" alt="生成图片">`;else $('#imageResult').innerHTML='暂无结果'}
$('#generateImage').onclick=async()=>{try{const payload={prompt:$('#imagePrompt').value,ratio:$('#imageRatio').value,parent_version_id:state.deepMode?state.deepParentVersionId:null,level:state.deepMode?$('#deepLevel').value:'normal',change_ratio:state.deepMode?$('#changeImageRatio').checked:true};if(!state.deepMode&&state.imageMode==='image'){if(!state.imageSourceAssets.length){toast('请先上传图生图起始图片',true);return}payload.source_asset_id=state.imageSourceAssets[0].id}const r=await post('/api/image/generate',payload);if(r.version?.asset)setCurrentImage(r.version.asset);if(r.task?.status==='result_pending'&&window.showPendingImageSave)window.showPendingImageSave(r.task);toast(r.task?.status==='completed'?'图片已生成':(r.reason||'任务已提交'));await refreshImageVersions();syncAccountBalanceBurst()}catch(e){toast(e.message,true)}};
async function refreshImageVersions(){try{const [h,assets]=await Promise.all([api('/api/history'),api('/api/assets')]);const map=Object.fromEntries(assets.map(a=>[a.id,a]));state.imageVersions=(h.versions||[]).filter(v=>v.kind==='image').map(v=>({...v,asset:map[v.asset_id]}));$('#imageVersions').innerHTML=state.imageVersions.map(v=>{const meta=v.metadata||{};const modeLabel=meta.mode==='image2image'?'图生图':(meta.mode==='deep'?'深度生成':'文生图');return `<div class="version-card">${v.asset?`<img src="${v.asset.url}" alt="版本图片">`:''}<small>${esc(v.version_id)}<br>${esc(modeLabel)} / ${esc(v.level||'normal')}</small><button class="secondary" data-view-img="${esc(v.version_id)}">查看此版</button><button class="secondary" data-reuse-img="${esc(v.version_id)}">复用生成</button><button class="secondary" data-deep="${esc(v.version_id)}">从此版深度生成</button><button class="secondary" data-use-img-source="${esc(v.version_id)}">设为图生图起点</button></div>`}).join('')||'<div class="muted tiny">还没有图片版本。</div>';$$('[data-reuse-img]').forEach(b=>b.onclick=()=>{const v=state.imageVersions.find(x=>x.version_id===b.dataset.reuseImg);if(!v)return;
 const meta=v.metadata||{};$('#imagePrompt').value=normalizePromptMentions(v.prompt||'');if(meta.ratio&&[...$('#imageRatio').options].some(x=>x.value===meta.ratio))$('#imageRatio').value=meta.ratio;
 if(meta.mode==='deep'){state.deepMode=true;state.deepParentVersionId=v.parent_version_id||v.version_id;$('#deepModeBtn').classList.add('active');$('#deepLevelWrap').classList.remove('hidden');$('#deepSourceBox').classList.remove('hidden');$('#changeRatioWrap').classList.remove('hidden');renderDeepSource();}
 else{state.deepMode=false;$('#deepModeBtn').classList.remove('active');$('#deepLevelWrap').classList.add('hidden');$('#deepSourceBox').classList.add('hidden');$('#changeRatioWrap').classList.add('hidden');}
 if(meta.mode==='image2image'){const a=state.imageVersions.find(x=>x.asset?.id===meta.source_asset_id)?.asset||state.assetLibrary.find(x=>x.id===meta.source_asset_id);if(a){state.imageSourceAssets=[a];renderImageSourceList();setImageMode('image')}else{setImageMode('image');state.imageSourceAssets=[];renderImageSourceList();toast('历史图生图素材缺失，请重新上传原始素材。',true)}}
 else if(meta.mode!=='deep'){setImageMode('text')}
 if(v.asset)setCurrentImage(v.asset);scheduleDraftSave();toast('已恢复历史图片提示词与现存参数，修改后生成新的图片版本。');
 });$$('[data-view-img]').forEach(b=>b.onclick=()=>{const v=state.imageVersions.find(x=>x.version_id===b.dataset.viewImg);if(v?.asset)setCurrentImage(v.asset)});$$('[data-deep]').forEach(b=>b.onclick=()=>{state.deepMode=true;state.deepParentVersionId=b.dataset.deep;$('#deepModeBtn').classList.add('active');$('#deepLevelWrap').classList.remove('hidden');$('#deepSourceBox').classList.remove('hidden');$('#changeRatioWrap').classList.remove('hidden');$('#changeImageRatio').checked=false;renderDeepSource();toast('已选择深度生成起点')});$$('[data-use-img-source]').forEach(b=>b.onclick=()=>{const v=state.imageVersions.find(x=>x.version_id===b.dataset.useImgSource);if(v?.asset){state.imageSourceAssets=[v.asset];renderImageSourceList();setImageMode('image');toast('已设为图生图起始图片')}})}catch(e){toast(e.message,true)}}
$('#addImageToVideo').onclick=()=>{if(!state.currentImageAsset)return;if(!state.videoRefs.some(x=>x.id===state.currentImageAsset.id))state.videoRefs.push(state.currentImageAsset);renderAssets();toast('已加入视频参考素材')};
$('#addImageToReplica').onclick=()=>{if(!state.currentImageAsset)return;if(!state.replicaProducts.some(x=>x.id===state.currentImageAsset.id))state.replicaProducts.push(state.currentImageAsset);renderAssets();toast('已加入爆款复刻产品图')};
$('#useCurrentAsImageSource')?.addEventListener('click',()=>{if(!state.currentImageAsset)return;state.imageSourceAssets=[state.currentImageAsset];renderImageSourceList();setImageMode('image');toast('当前结果已设为图生图起点')});

async function refreshHistory(){try{const [h,c,r]=await Promise.all([api('/api/history'),api('/api/cost/summary'),api('/api/tasks/recovery')]);const t=c.total||{};$('#costSummary').innerHTML=`<table class="preview-table"><tr><td>已记录付费调用</td><td>${t.n||0} 次</td></tr><tr><td>估算费用合计</td><td>${Number(t.est||0)>0?'$'+Number(t.est).toFixed(4):'未配置单价 / 无估算'}</td></tr><tr><td>实际费用合计</td><td>${Number(t.actual||0)>0?'$'+Number(t.actual).toFixed(4):'以 OpenLux 后台账单为准'}</td></tr></table><p class="tiny muted">系统不会把“未知价格”当成免费。建议按你的 OpenLux 实际结算价填写 .env 费用字段。</p>`;$('#taskHistory').innerHTML=(h.tasks||[]).map(x=>`<div class="history-item"><b>${esc(x.kind)} · ${esc(statusLabel(x.status))}</b><span>${esc(x.id)} · ${fmtTime(x.created_at)}</span>${x.provider_task_id?`<span>平台任务号：${esc(x.provider_task_id)}</span>`:''}${x.error?`<span class="bad">${esc(x.error)}</span>`:''}${x.last_query_error?`<span class="warn">最近查询失败：${esc(x.last_query_error)}</span>`:''}<button type="button" class="secondary" onclick="window.reuseSeedanceTask('${esc(x.id)}')">复用生成</button></div>`).join('')||'<div class="muted">暂无任务。</div>';renderRecoveryCenter(r.rows||[])}catch(e){toast(e.message,true)}}
function renderRecoveryCenter(rows){const box=$('#taskRecovery');if(!box)return;if(!rows.length){box.innerHTML='<div class="workspace-empty compact"><b>没有需要恢复的任务</b><span>运行中、结果待同步、本地保存失败或提交结果不确定的任务会出现在这里。</span></div>';return}box.innerHTML=rows.map(x=>{const r=x.result||{};const isActive=['pending','running','result_pending'].includes(x.status);const attention=x.status==='submission_unknown';const download=!!r.download_error&&!!r.remote_url;const tag=isActive?'<span class="recovery-tag recovery-running">继续跟踪</span>':attention?'<span class="recovery-tag recovery-attention">需人工核对</span>':download?'<span class="recovery-tag recovery-download">待重新下载</span>':'<span class="recovery-tag recovery-ok">可恢复</span>';return `<div class="history-item"><b>${esc(statusLabel(x.status))}${tag}</b><span>${esc(x.id)} · ${fmtTime(x.updated_at||x.created_at)}</span>${x.provider_task_id?`<span>平台任务号：${esc(x.provider_task_id)}</span>`:''}${x.last_query_error?`<span class="warn">最近查询：${esc(x.last_query_error)}</span>`:''}${r.download_error?`<span class="warn">本地保存：${esc(r.download_error)}</span>`:''}<div class="history-action-row">${!x.provider_task_id&&x.status!=='completed'?`<button class="secondary" data-recovery-attach="${esc(x.id)}">绑定平台任务号</button>`:''}${x.provider_task_id&&x.status!=='completed'?`<button class="secondary" data-recovery-refresh="${esc(x.id)}">重新查询状态</button>`:''}${download?`<button class="secondary" data-recovery-download="${esc(x.id)}">重新下载结果</button>`:''}</div></div>`}).join('');$$('[data-recovery-refresh]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post('/api/tasks/refresh',{task_id:b.dataset.recoveryRefresh});toast('已重新查询平台状态');await refreshHistory();await restorePersistedTasks()}catch(e){toast(e.message,true)}finally{b.disabled=false}});$$('[data-recovery-download]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post('/api/tasks/retry-download',{task_id:b.dataset.recoveryDownload});toast('结果已重新下载到本地');await refreshHistory();await restorePersistedTasks()}catch(e){toast(e.message,true)}finally{b.disabled=false}});$$('[data-recovery-attach]').forEach(b=>b.onclick=async()=>{const pid=prompt('请粘贴 OpenLux 后台显示的视频任务号。系统只会查询并下载原任务，不会重新提交付费生成。');if(!pid)return;b.disabled=true;try{await post('/api/tasks/attach-provider',{task_id:b.dataset.recoveryAttach,provider_task_id:pid,confirmed:true});toast('已绑定平台任务号，正在查询原任务');await refreshHistory();await restorePersistedTasks()}catch(e){toast(e.message,true)}finally{b.disabled=false}})}
async function refreshActivity(){try{const a=await api('/api/activity');$('#activityLog').innerHTML=(a.rows||[]).map(x=>`<div class="history-item"><b>${esc(x.kind)} · ${esc(statusLabel(x.status))}</b><span>实际模型：${esc(a.model)}</span><span>${esc(x.id)} · ${fmtTime(x.updated_at)}</span></div>`).join('')||'<div class="muted">暂无后台任务。</div>'}catch(e){toast(e.message,true)}}

$('#startTunnel').onclick=async()=>{try{const x=await post('/api/tunnel/start',{});toast(x.running?`快速隧道已启动：${x.url}`:(x.error||'启动失败'),!x.running);refreshStatus()}catch(e){toast(e.message,true)}};
$('#stopTunnel').onclick=async()=>{try{await post('/api/tunnel/stop',{});toast('快速隧道已停止');refreshStatus()}catch(e){toast(e.message,true)}};
$('#testOSS').onclick=async()=>{try{const cfg={oss_endpoint:$('#setOSSEndpoint').value.trim(),oss_bucket:$('#setOSSBucket').value.trim(),oss_object_prefix:$('#setOSSPrefix').value.trim()||'seedance-studio',asset_link_ttl_minutes:+$('#setAssetTtl').value,oss_auto_cleanup:$('#setOSSAutoCleanup').checked,oss_delete_after_hours:+$('#setOSSDeleteHours').value,public_asset_strategy:$('#setPublicStrategy').value};const ak=$('#setOSSAccessKey').value.trim(),sk=$('#setOSSSecretKey').value.trim();await post('/api/oss/config',{...cfg,oss_access_key_id:ak||null,oss_access_key_secret:sk||null});const x=await post('/api/oss/test',{});const rows=(x.checks||[]).map(c=>`${c.ok?'✓':'✗'} ${esc(c.name)}${c.status?' · HTTP '+c.status:''}${c.error?' · '+esc(c.error):''}`).join('<br>');$('#tunnelTestResult').innerHTML=`使用通道：阿里云 OSS<br>${rows}${x.error?'<br><span class="bad">'+esc(x.error)+'</span>':''}`;if(x.ok){$('#setOSSAccessKey').value='';$('#setOSSSecretKey').value=''}toast(x.ok?'OSS 配置测试通过并已保存':'OSS 配置测试失败',!x.ok);refreshStatus()}catch(e){toast(e.message,true)}};
$('#cleanupOSS').onclick=async()=>{try{const x=await post('/api/oss/cleanup',{});toast(`OSS 清理完成：删除 ${x.deleted||0} 个过期对象`);refreshStatus()}catch(e){toast(e.message,true)}};
$('#testTunnel').onclick=async()=>{try{
 const x=await post('/api/public-assets/selftest',{asset_id:state.replicaVideo?.id||state.videoRefs[0]?.id||null});
 const assetLine=x.tested_asset_name?`测试素材：${esc(x.tested_asset_name)}<br>`:'';
 const rows=(x.checks||[]).map(c=>{const req=c.required?'【必需】':'【增强】';const err=c.error?` · ${esc(c.error)}`:'';return `${c.ok?'✓':'✗'} ${req} ${esc(c.name)}${c.status?' · HTTP '+c.status:''}${err}`}).join('<br>');
 const level=x.ok?'可用':'不可用';
 $('#tunnelTestResult').innerHTML=(x.provider?`使用通道：${esc(x.provider)}<br>`:'')+assetLine+`兼容等级：<b>${esc(level)}</b><br>`+rows+(x.warning?`<br><span class="warn">${esc(x.warning)}</span>`:'')+(x.error?`<br><span class="bad">${esc(x.error)}</span>`:'');
 await refreshStatus();
 toast(x.ok?'公网素材自检通过，可继续生成':'公网素材自检未通过',!x.ok)
}catch(e){toast(e.message,true)}};
$('#bindOpenLuxAccount')?.addEventListener('click',async()=>{
 try{
   const token=$('#setOpenLuxAccountToken')?.value?.trim()||'';
   const userId=$('#setOpenLuxAccountUserId')?.value?.trim()||'';
   if(!token){toast('请输入 OpenLux 账号访问令牌；只需绑定一次，之后跨版本复用。',true);return}
   const r=await post('/api/openlux/account-credentials',{openlux_account_access_token:token,openlux_account_user_id:userId});
   $('#setOpenLuxAccountToken').value='';
   toast('OpenLux 账户余额绑定完成');
   renderCostHud(r.cost);
   await refreshCostHud(true);
 }catch(e){toast(e.message,true)}
});
$('#clearOpenLuxAccount')?.addEventListener('click',async()=>{
 try{await post('/api/openlux/account-clear',{});toast('已清除 OpenLux 账户绑定');await refreshCostHud(true)}catch(e){toast(e.message,true)}
});
$('#saveSettings').onclick=async()=>{try{const v={
 video_base_model:$('#setVideoModelChoice').value==='custom'?$('#setVideoBaseModel').value.trim():$('#setVideoModelChoice').value,video_route_control:$('#setVideoRouteControl').value,video_route_preference:$('#setVideoRoutePreference').value,video_endpoint_strategy:$('#setEndpointStrategy').value,custom_video_create_path:$('#setVideoCreatePath').value.trim(),custom_video_query_path:$('#setVideoQueryPath').value.trim(),
 default_video_mode:$('#setVideoMode').value,default_video_ratio:$('#setVideoRatio').value,default_video_resolution:$('#setResolution').value,default_video_duration:+$('#setDuration').value,default_image_ratio:$('#setImageRatio').value,image_quality:$('#setImageQuality').value,
 dedupe_seconds:+$('#setDedupeSeconds').value,max_concurrent_video_tasks:+$('#setMaxConcurrent').value,http_timeout:+$('#setHttpTimeout').value,query_retries:+$('#setQueryRetries').value,
 cost_auto_balance:true,cost_manual_balance:0,cost_quota_per_usd:+$('#setCostQuotaPerUsd').value,cost_fallback_cny_per_second:+$('#setCostFallbackCnyPerSecond').value,cost_fx_refresh_minutes:+$('#setCostFxRefreshMinutes').value,paid_budget_usd:+$('#setPaidBudgetUsd').value,paid_budget_window_hours:+$('#setPaidBudgetWindowHours').value,paid_budget_max_tasks:+$('#setPaidBudgetMaxTasks').value,cost_video_720_low_per_second:0,cost_video_720_high_per_second:0,cost_video_480_multiplier:1,cost_video_1080_multiplier:1,
 replica_scene_threshold:+$('#setSceneThreshold').value,replica_max_scenes:+$('#setMaxScenes').value,replica_transcript_mode:$('#setTranscript').value,replica_ocr_mode:$('#setOcr').value,replica_inherit_duration:$('#setInheritDuration').checked,replica_inherit_ratio:$('#setInheritRatio').checked,replica_require_analysis_gate:$('#setAnalysisGate').checked,replica_require_word_anchor_for_paid:$('#setWordAnchorGate').checked,replica_spatial_analysis:$('#setSpatialAnalysis').checked,auto_quality_check:$('#setAutoQualityCheck').checked,
 public_asset_strategy:$('#setPublicStrategy').value,tunnel_auto_start:false,tunnel_selftest_before_paid:$('#setTunnelSelftest').checked,fixed_public_asset_base_url:$('#setFixedPublicUrl').value.trim(),asset_link_ttl_minutes:+$('#setAssetTtl').value,
 oss_endpoint:$('#setOSSEndpoint').value.trim(),oss_bucket:$('#setOSSBucket').value.trim(),oss_object_prefix:$('#setOSSPrefix').value.trim(),oss_auto_cleanup:$('#setOSSAutoCleanup').checked,oss_delete_after_hours:+$('#setOSSDeleteHours').value};
 await post('/api/settings',v);
 const ak=$('#setOSSAccessKey').value.trim(),sk=$('#setOSSSecretKey').value.trim();
 if(ak||sk)await post('/api/oss/config',{oss_access_key_id:ak||null,oss_access_key_secret:sk||null});
 $('#setOSSAccessKey').value='';$('#setOSSSecretKey').value='';state._defaultsApplied=false;await refreshStatus();toast('全部设置已保存并立即生效') }catch(e){toast(e.message,true)}};

$('#resetSettings').onclick=async()=>{if(!confirm('恢复推荐默认设置？不会删除历史、素材或 .env。'))return;try{await post('/api/settings/reset',{});state._defaultsApplied=false;await refreshStatus();toast('已恢复推荐默认设置')}catch(e){toast(e.message,true)}};


// ===== V3.9.0 本地工作区草稿 / 素材库 / 系统体检 =====
function activePageName(){return $('.nav[data-page].active')?.dataset.page||'video'}
function activeWorkspaceTab(group){const b=$$(`[data-workspace-tab^="${group}:"]`).find(x=>x.classList.contains('active'));return b?.dataset?.workspaceTab?.split(':')[1]||null}
function captureDraft(){
 return {
  page:activePageName(),scrollY:Math.max(0,window.scrollY||0),savedAt:Date.now()/1000,
  video:{selected_model:$('#videoModelChoice')?.value||'',prompt:$('#videoPrompt')?.value||'',mode:$('#videoMode')?.value||'auto',ratio:$('#videoRatio')?.value||'9:16',resolution:$('#videoResolution')?.value||'720p',duration:+($('#videoDuration')?.value||10),asset_ids:(state.videoRefs||[]).map(x=>x.id),audio_reference_mode:selectedAudioReferenceMode('video'),tab:activeWorkspaceTab('video')||'preview'},
  replica:{resolution:$('#replicaResolution')?.value||'720p',selected_model:$('#replicaModelChoice')?.value||'',instruction:$('#replicaInstruction')?.value||'',video_id:state.replicaVideo?.id||null,product_ids:(state.replicaProducts||[]).map(x=>x.id),audio_ids:(state.replicaAudios||[]).map(x=>x.id),audio_reference_mode:selectedAudioReferenceMode('replica'),workflow_version_id:state.workflowVersionId||null,workflow_group_id:state.workflowGroupId||null,workflow_confirmed:!!state.workflowConfirmed,tab:activeWorkspaceTab('replica')||'ready'},
  image:{prompt:$('#imagePrompt')?.value||'',ratio:$('#imageRatio')?.value||'9:16',mode:state.imageMode||'text',source_ids:(state.imageSourceAssets||[]).map(x=>x.id),deep_mode:!!state.deepMode,deep_parent_version_id:state.deepParentVersionId||null,deep_level:$('#deepLevel')?.value||'medium',change_ratio:!!$('#changeImageRatio')?.checked}
 }
}
async function saveDraftNow(){if(state.draftRestoring)return;try{const d=captureDraft();await post('/api/workspace/draft',{workspace:'global',data:d});state.lastDraftSavedAt=Date.now();const el=$('#draftStatus');if(el)el.textContent='已自动保存'}catch(e){console.warn('draft save failed',e)}}
function scheduleDraftSave(){if(state.draftRestoring)return;clearTimeout(state.draftTimer);const el=$('#draftStatus');if(el)el.textContent='保存中…';state.draftTimer=setTimeout(saveDraftNow,650)}
async function restoreWorkspaceDraft(){
 state.draftRestoring=true;
 try{
  const [drafts,assets]=await Promise.all([api('/api/workspace/drafts'),api('/api/assets')]);const d=drafts?.global?.data;if(!d)return false;
  const map=Object.fromEntries((assets||[]).map(a=>[a.id,a]));const v=d.video||{},r=d.replica||{},im=d.image||{};
  if($('#videoModelChoice'))$('#videoModelChoice').value=['auto','default'].includes(v.selected_model)?'':(v.selected_model||'');if($('#replicaModelChoice'))$('#replicaModelChoice').value=['auto','default'].includes(r.selected_model)?'':(r.selected_model||'');
 if($('#videoPrompt'))$('#videoPrompt').value=normalizePromptMentions(v.prompt||'');if($('#videoMode')&&v.mode)$('#videoMode').value=v.mode;if($('#videoRatio')&&v.ratio)$('#videoRatio').value=v.ratio;if($('#videoResolution')&&v.resolution)$('#videoResolution').value=v.resolution;if($('#videoDuration')&&v.duration)$('#videoDuration').value=v.duration;if($('#replicaResolution')&&r.resolution)$('#replicaResolution').value=r.resolution;
  state.videoRefs=(v.asset_ids||[]).map(id=>map[id]).filter(Boolean);
  if($('#replicaInstruction'))$('#replicaInstruction').value=normalizePromptMentions(r.instruction||'');state.replicaVideo=r.video_id?map[r.video_id]||null:null;state.replicaProducts=(r.product_ids||[]).map(id=>map[id]).filter(x=>x?.kind==='image');state.replicaAudios=(r.audio_ids||[]).map(id=>map[id]).filter(x=>x?.kind==='audio');
  for(const [group,choice] of [['videoAudioUse',v.audio_reference_mode],['replicaAudioUse',r.audio_reference_mode]]){const input=document.querySelector(`input[name="${group}"][value="${choice||'timbre'}"]`);if(input)input.checked=true}
  state.workflowVersionId=r.workflow_version_id||null;state.workflowGroupId=r.workflow_group_id||null;state.workflowConfirmed=!!r.workflow_confirmed;
  if($('#imagePrompt'))$('#imagePrompt').value=normalizePromptMentions(im.prompt||'');if($('#imageRatio')&&im.ratio)$('#imageRatio').value=im.ratio;state.imageSourceAssets=(im.source_ids||[]).map(id=>map[id]).filter(Boolean);state.deepMode=!!im.deep_mode;state.deepParentVersionId=im.deep_parent_version_id||null;if($('#deepLevel')&&im.deep_level)$('#deepLevel').value=im.deep_level;if($('#changeImageRatio'))$('#changeImageRatio').checked=!!im.change_ratio;
  updateModeUi();updateResolutionControls({silent:true});renderAssets();setImageMode(im.mode||'text');renderImageSourceList();
  $('#deepModeBtn')?.classList.toggle('active',state.deepMode);$('#deepLevelWrap')?.classList.toggle('hidden',!state.deepMode);$('#deepSourceBox')?.classList.toggle('hidden',!state.deepMode);$('#changeRatioWrap')?.classList.toggle('hidden',!state.deepMode);if(state.deepMode)renderDeepSource();
  if(v.tab)setWorkspaceTab('video',v.tab);if(r.tab)setWorkspaceTab('replica',r.tab);if(!state.navigationTouched)activatePage(d.page||'video',{save:false});
  if(!state.navigationTouched)setTimeout(()=>{if(!state.navigationTouched)window.scrollTo({top:Number(d.scrollY||0),behavior:'auto'})},80);
  const el=$('#draftStatus');if(el)el.textContent=`已恢复上次工作区${drafts.global.updated_at?' · '+new Date(drafts.global.updated_at*1000).toLocaleTimeString():''}`;
  return true;
 }catch(e){console.warn('draft restore failed',e);return false}finally{state.draftRestoring=false}
}
const DRAFT_IDS=new Set(['videoModelChoice','replicaModelChoice','videoPrompt','videoMode','videoRatio','videoResolution','videoDuration','replicaResolution','replicaInstruction','imagePrompt','imageRatio','deepLevel','changeImageRatio']);
document.addEventListener('input',e=>{if(DRAFT_IDS.has(e.target?.id))scheduleDraftSave()});document.addEventListener('change',e=>{if(DRAFT_IDS.has(e.target?.id))scheduleDraftSave()});window.addEventListener('beforeunload',()=>{try{const d=captureDraft();navigator.sendBeacon?.('/api/workspace/draft',new Blob([JSON.stringify({workspace:'global',data:d})],{type:'application/json'}))}catch{}});

function libraryActionButtons(a){const addVideo=`<button class="secondary" data-lib-video="${esc(a.id)}">加入视频创作</button>`;const image=a.kind==='image'?`<button class="secondary" data-lib-image-source="${esc(a.id)}">设为图生图起点</button>`:'';return addVideo+image+`<button type="button" class="secondary" data-lib-remove="${esc(a.id)}">删除</button>`}
async function refreshAssetLibrary(){const box=$('#assetLibraryGrid');if(!box)return;try{const kind=$('#assetLibraryKind')?.value||'',q=$('#assetLibrarySearch')?.value?.trim()||'';const qs=new URLSearchParams();if(kind)qs.set('kind',kind);if(q)qs.set('q',q);const rows=await api('/api/asset-library?'+qs.toString());state.assetLibrary=rows||[];$('#assetLibraryCount').textContent=`${rows.length} 个素材`;box.innerHTML=rows.length?rows.map(a=>`<article class="library-card">${assetPreview(a)}<div class="library-card-main"><b>${esc(a.original_name)}</b><span>${esc(kindLabel(a.kind))}${a.width&&a.height?` · ${a.width}×${a.height}`:''}${a.duration?` · ${Number(a.duration).toFixed(2)}s`:''}</span><small>${new Date((a.created_at||0)*1000).toLocaleString()}</small></div><div class="library-actions">${libraryActionButtons(a)}</div></article>`).join(''):'<div class="workspace-empty"><b>没有匹配的本地素材</b></div>';bindLibraryActions()}catch(e){$('#assetLibraryCount').textContent='读取失败';box.innerHTML=`<div class="bad">读取素材库失败：${esc(e.message)}</div>`}}
function bindLibraryActions(){const find=id=>state.assetLibrary.find(x=>x.id===id);$$('[data-lib-remove]').forEach(b=>b.onclick=async()=>{try{await window.removeStudioUpload(b.dataset.libRemove);await refreshAssetLibrary()}catch(e){toast(e.message,true)}});$$('[data-lib-video]').forEach(b=>b.onclick=()=>{const a=find(b.dataset.libVideo);if(a&&!state.videoRefs.some(x=>x.id===a.id)){state.videoRefs.push(a);renderAssets();invalidatePreviews()}activatePage('video');toast('已加入视频创作参考素材')});$$('[data-lib-rep-video]').forEach(b=>b.onclick=()=>{const a=find(b.dataset.libRepVideo);if(a){state.replicaVideo=a;renderAssets();resetReplicaWorkflow();scheduleDraftSave()}activatePage('replica');toast('已设为爆款复刻参考视频')});$$('[data-lib-rep-product]').forEach(b=>b.onclick=()=>{const a=find(b.dataset.libRepProduct);if(a&&!state.replicaProducts.some(x=>x.id===a.id)){state.replicaProducts.push(a);renderAssets();invalidateReplicaPreparation('产品参考图已变化，请重新准备生成。')}activatePage('replica');toast('已加入爆款复刻产品图')});$$('[data-lib-image-source]').forEach(b=>b.onclick=()=>{const a=find(b.dataset.libImageSource);if(a){state.imageSourceAssets=[a];setImageMode('image');renderImageSourceList()}activatePage('image');toast('已设为图生图起始图片')})}
$('#refreshAssetLibrary')?.addEventListener('click',refreshAssetLibrary);$('#assetLibraryKind')?.addEventListener('change',refreshAssetLibrary);let libTimer=null;$('#assetLibrarySearch')?.addEventListener('input',()=>{clearTimeout(libTimer);libTimer=setTimeout(refreshAssetLibrary,250)});

function healthClass(x){return x.ok?'health-ok':(x.level==='required'?'health-bad':'health-warn')}
async function refreshSystemHealth(){const box=$('#systemHealthGrid');if(!box)return;try{const h=await api('/api/system/health');$('#systemHealthSummary').innerHTML=h.required_ok?`<span class="ok"><b>核心能力可用</b></span>${h.recommended_ok?' · 全部增强能力已就绪':' · 部分增强能力可按安全安装方法补齐'}`:`<span class="bad"><b>发现核心能力缺失</b></span> · 请先查看安全安装方法`;box.innerHTML=(h.checks||[]).map(x=>`<div class="health-item ${healthClass(x)}"><div><b>${x.ok?'✓':'!'} ${esc(x.label)}</b><span>${esc(x.level==='required'?'必须':'增强')}</span></div><small>${esc(x.detail||'')}</small></div>`).join('');renderRepairProgress(h.repair_job)}catch(e){$('#systemHealthSummary').innerHTML=`<span class="bad">体检失败：${esc(e.message)}</span>`}}
function renderRepairProgress(j){const el=$('#repairProgress');if(!el||!j)return;clearTimeout(state.healthTimer);state.healthTimer=null;if(j.running){el.innerHTML='<span class="warn">正在自动安装 / 修复本地分析组件，请不要关闭 Seedance Studio…</span>';state.healthTimer=setTimeout(async()=>{try{const x=await api('/api/system/repair');if(x.running)renderRepairProgress(x);else await refreshSystemHealth()}catch(e){el.textContent='获取修复状态失败：'+e.message}},2500)}else if(j.finished_at){el.innerHTML=j.exit_code===0?'<span class="ok">一键修复已完成。</span>':`<span class="warn">${esc(j.error||'部分增强组件未安装成功。')} ${esc(j.log_tail||'')}</span>`}}
$('#runSystemHealth')?.addEventListener('click',refreshSystemHealth);$('#repairSystem')?.addEventListener('click',()=>{const el=$('#repairProgress');if(el){el.style.whiteSpace='pre-line';el.textContent='安全安装步骤：\n1. 等待本机所有生成、聊天和上传任务结束，先保存工作。\n2. 在本机暂停远程入口并正常关闭视频后台（勿只关闭浏览器标签页）。\n3. 在当前视频平台目录运行 01_INSTALL.cmd，按安装结果处理。\n4. 安装完成后重新启动平台并恢复远程入口，再点击“重新体检”。\n安装会修改本机运行环境，不能在运行中的网页里覆盖安装。';}toast('已显示安全安装方法，未启动安装、未关闭任务。')});
async function createMigrationBackup(includeOutputs=false){try{const b=await post('/api/backup/create',{include_outputs:includeOutputs});$('#backupResult').textContent=`已创建：${b.name}`;toast('迁移备份已创建到 data/backups')}catch(e){toast(e.message,true)}}
$('#createBackup')?.addEventListener('click',()=>createMigrationBackup(false));$('#createBackupWithOutputs')?.addEventListener('click',()=>createMigrationBackup(true));

async function refreshCredentialStatus(){
 try{const x=await api('/api/credentials/status');const el=$('#localCredentialsStatus');if(el)el.textContent=(x.openlux_api_key_configured?'接口密钥已保存':'接口密钥待配置')+' · '+(x.oss_access_key_configured?'存储凭据已保存':'存储凭据待配置')+' · '+(x.storage||'本机存储');}
 catch(e){const el=$('#localCredentialsStatus');if(el)el.textContent='读取本机凭据失败：'+e.message;}
}
$('#saveLocalCredentials')?.addEventListener('click',async()=>{
 const key=$('#setOpenLuxApiKey')?.value.trim()||'';
 if(!key){toast('未输入新密钥，将继续沿用已保存的凭据');await refreshCredentialStatus();return}
 try{await post('/api/credentials/save',{openlux_api_key:key});$('#setOpenLuxApiKey').value='';await refreshCredentialStatus();await refreshStatus();toast('密钥已安全保存到本机，后续启动自动读取');}
 catch(e){toast('保存失败：'+e.message,true)}
});
$('#modelSettingsShortcut').onclick=()=>{const b=$('.nav[data-page="settings"]');if(b)b.click();setTimeout(()=>$('#setVideoRouteControl')?.focus(),50)};
$('#openOutput').onclick=()=>openOutputFolder();
$('#refreshAll').onclick=async()=>{await saveDraftNow();await refreshStatus();refreshCostHud(true);refreshImageVersions();refreshHistory();refreshActivity();restorePersistedTasks();refreshTaskCenter(false);if(activePageName()==='assets')refreshAssetLibrary();if(activePageName()==='settings')refreshSystemHealth();toast('已刷新，工作区内容已保留')};

// V3.9.0: status defaults load first, then the persisted local draft overrides them.
async function bootstrapStudio(){
 state.draftRestoring=true;
 try{bindMentionInput('#videoPrompt','video');bindMentionInput('#replicaInstruction','replica');bindMentionInput('#imagePrompt','image')}catch(e){console.error('mention init failed',e)}
 try{setWorkspaceTab('video','preview');setWorkspaceTab('replica','ready');renderAssets();setImageMode('text');renderImageSourceList()}catch(e){console.error('workspace init failed',e)}
 await refreshStatus();await refreshCredentialStatus();await restoreWorkspaceDraft();
 refreshImageVersions();restorePersistedTasks();refreshTaskCenter(false);refreshCostHud(false);
 if(activePageName()==='assets')refreshAssetLibrary();if(activePageName()==='settings')refreshSystemHealth();
 state.taskCenterTimer=setInterval(()=>refreshTaskCenter(true),4000);state.costTimer=setInterval(()=>refreshCostHud(false),30000);
 // Non-blocking version check — runs after UI is up, never delays startup.
 setTimeout(checkForUpdate, 4000);
}
bootstrapStudio();

async function checkForUpdate(){
 try{
  // api() already unwraps the {ok,data} envelope and throws on failure.
  const d=await api('/api/version/check');
  if(!d||!d.update_available)return;
  // Skip if user already dismissed this specific release in this session.
  const dismissedKey='seedance_update_dismissed_'+d.remote_version+'_'+d.remote_build;
  if(sessionStorage.getItem(dismissedKey))return;
  const note=(d.release_notes||'').substring(0,200);
  const download=d.installer_url;
  const body=document.createElement('div');
  body.style.cssText='position:fixed;top:12px;right:12px;z-index:99999;max-width:360px;background:#fff4e5;border:1px solid #e6a23c;border-radius:8px;padding:12px 14px;box-shadow:0 4px 18px rgba(0,0,0,0.15);font:14px/1.5 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#333;';
  body.innerHTML=
   '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'+
    '<b style="color:#d46b08;">🆕 有新版本可用</b>'+
    '<button id="seedUpdateDismiss" style="border:none;background:transparent;cursor:pointer;font-size:18px;color:#999;line-height:1;">×</button>'+
   '</div>'+
   '<div style="margin-bottom:8px;">当前：<b>'+esc(d.local_version)+' '+esc(d.local_build)+'</b> → <b>'+esc(d.remote_version)+' '+esc(d.remote_build)+'</b> ('+esc(d.release_date||'')+')</div>'+
   (note?'<div style="margin-bottom:10px;color:#555;font-size:13px;">'+esc(note)+'</div>':'')+
   '<div style="display:flex;gap:8px;">'+
    (download?'<a href="'+esc(download)+'" target="_blank" rel="noopener" style="display:inline-block;padding:6px 14px;background:#ff7d00;color:#fff;border-radius:5px;text-decoration:none;font-size:13px;">下载更新</a>':'')+
    '<button id="seedUpdateLater" style="padding:6px 12px;background:#f0f0f0;color:#666;border:1px solid #ddd;border-radius:5px;cursor:pointer;font-size:13px;">稍后提醒</button>'+
   '</div>';
  document.body.appendChild(body);
  const close=()=>{sessionStorage.setItem(dismissedKey,'1');body.remove();};
  body.querySelector('#seedUpdateDismiss').onclick=close;
  body.querySelector('#seedUpdateLater').onclick=close;
 }catch(e){/* network failure or backend not ready — silently skip */}
}

updateVideoRouteStatus();

// Read-only capability sync; never submits video-generation requests.
loadResolutionProfiles();

window.reuseSeedanceTask=reuseHistoricalTask;


// A global library deletion removes current references and invalidates paid previews.
window.removeStudioUpload=async function(id){
 const result=await post('/api/uploads/remove',{kind:'asset',id});
 const videoBefore=[...state.videoRefs],replicaBefore=mentionScopeAssets('replica'),imageBefore=[...state.imageSourceAssets];
 state.videoRefs=state.videoRefs.filter(x=>x.id!==id);
 state.replicaProducts=state.replicaProducts.filter(x=>x.id!==id);
 state.replicaAudios=state.replicaAudios.filter(x=>x.id!==id);
 if(state.replicaVideo?.id===id)state.replicaVideo=null;
 state.imageSourceAssets=state.imageSourceAssets.filter(x=>x.id!==id);
 if(state.currentImageAsset?.id===id){state.currentImageAsset=null;state.deepMode=false;state.deepParentVersionId=null;}
 state.assetLibrary=state.assetLibrary.filter(x=>x.id!==id);
 rewriteAliasesAfterAssetChange('video',videoBefore,state.videoRefs,[id]);
 rewriteAliasesAfterAssetChange('replica',replicaBefore,mentionScopeAssets('replica'),[id]);
 rewriteAliasesAfterAssetChange('image',imageBefore,state.imageSourceAssets,[id]);
 renderAssets();renderImageSourceList();invalidatePreviews();resetReplicaWorkflow();scheduleDraftSave();
 window.dispatchEvent(new CustomEvent('seedance:asset-removed',{detail:{id}}));
 toast('已删除该可选素材；原文件与历史任务保留，不再参与新的生成。');
 return result;
};
