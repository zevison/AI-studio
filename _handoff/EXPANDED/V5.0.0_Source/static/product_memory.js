/* Product Knowledge Center V3.9.8.5 - transparent workflow, never silently disabled. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async(url,body)=>{const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};const r=await fetch(url,options),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'请求失败');return j.data};
const notify=(s,bad=false)=>{if(typeof toast==='function')toast(s,bad);else alert(s)};
let state=null,options=null,pid=null,preview=null,busy=false,dirty=false,priceCheckSeq=0;
let view='catalog';
function switchView(next){
 view=next==='editor'?'editor':'catalog';
 $('pmCatalogPanel').hidden=view!=='catalog';$('pmEditorPanel').hidden=view!=='editor';
 $('pmCatalogTab').classList.toggle('is-active',view==='catalog');
 $('pmEditorTab').classList.toggle('is-active',view==='editor');
}
async function reloadCatalog(){
 const items=await api('/api/product-memory/catalog');
 const area=$('pmCatalogCards');
 if(!items.length){area.innerHTML='<div class="pm-catalog-empty">还没有商品档案，点击“新建商品”开始上传资料。</div>';return}
 area.innerHTML=items.map(p=>`<article class="pm-catalog-card">
 <div class="pm-catalog-icon" aria-hidden="true">${p.image_count?`<img src="/api/product-memory/thumbnail?product_id=${encodeURIComponent(p.id)}" alt="" loading="lazy">`:(p.is_draft?'＋':'📦')}</div>
 <div class="pm-catalog-info"><b>${esc(p.name)}</b><div class="pm-catalog-stats">${p.source_count}份资料 · 商品知识第${p.memory_version}版 · ${p.keyword_count}条关键词 · ${p.verified_count}项已确认事实</div>
 <div class="pm-catalog-meta">热点跟踪：${p.active?'已启用':'未启用'} · 最后保存：${p.updated_at?new Date(p.updated_at*1000).toLocaleString():'未知'}</div></div>
 <button type="button" class="secondary pm-catalog-edit" data-pid="${esc(p.id)}">查看与修改</button></article>`).join('');
 area.querySelectorAll('.pm-catalog-edit').forEach(b=>b.addEventListener('click',()=>work(async()=>{
    if(dirty&&!confirm('当前商品有未保存修改，确定放弃并切换吗？'))return;
    await reload(b.dataset.pid);switchView('editor');
 })));
}

const rows=id=>($(id)?.value||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
const current=()=>state?.products?.find(p=>p.id===pid)||null;
const model=()=> $('pmModel').value==='custom' ? $('pmCustomModel').value.trim():$('pmModel').value;
async function work(fn){
 if(busy){notify('正在处理上一项操作，请稍后再试（没有重复提交）。',true);return}
 busy=true;try{await fn()}catch(err){setFeedback('操作未完成：'+err.message,true);notify(err.message,true)}finally{busy=false}
}
function setFeedback(t,bad=false){const el=$('pmSaveFeedback');if(el){el.textContent=t;el.classList.toggle('pm-bad',bad)}}
function changed(){dropPreview();dirty=true;setFeedback('有未保存的商品修改。点击「保存商品资料」后才会写入数据库。')}
function clearChanged(){dirty=false;setFeedback('商品资料已保存并回读验证。')}
function setAnalysisGate(message){$('pmAnalyze').disabled=false;$('pmAnalyze').textContent=message||'单次确认并用 GPT 分析';}

function dropPreview(){preview=null;setAnalysisGate();$('pmEstimate').textContent='请先点击「检查资料与预估费用」；也可以直接点击分析按钮，由系统先自动进行免费的预检。';}
function factLines(){return rows('pmFacts').map(line=>{const a=line.split('|').map(t=>t.trim());const status=({'已确认':'verified','待验证':'unverified','禁止':'prohibited'})[a[0]]||'unverified';return {status,text:a.length>1?a[1]:line,source:a.slice(2).join('|')||''}})}
function updateProductSelect(){const select=$('pmProduct');select.innerHTML=(state.products||[]).map(p=>`<option value="${esc(p.id)}">${esc(p.is_draft?'新商品（待上传/识别）':p.name)}</option>`).join('');
 if(!pid||!state.products.some(p=>p.id===pid))pid=state.product_id||state.products[0]?.id||null;
 select.value=pid||'';
}
function fillForm(){const p=current();if(!p)return;
 $('pmName').value=p.is_draft?'':p.name;
 $('pmKeywords').value=(p.keywords||[]).join('\n');
 $('pmFacts').value=(p.facts||[]).map(f=>`${({verified:'已确认',unverified:'待验证',prohibited:'禁止'})[f.status]||'待验证'}|${f.text}${f.source?'|'+f.source:''}`).join('\n');
 $('pmRestrictions').value=(p.restrictions||[]).join('\n');
 $('pmActive').checked=!!p.active || (p.is_draft && !(p.keywords||[]).length);
 $('pmCurrentNotice').classList.toggle('pm-new',!!p.is_draft);
 $('pmCurrentNotice').textContent=p.is_draft ? '当前是全新商品，尚未关联 AIR3。先上传链接、文档或产品图，再由 GPT 识别并生成关键词；视频采集只接受手动触发。' : `当前商品：${p.name} · ${(state.sources||[]).length}份资料 · ${(p.keywords||[]).length}个热点检索词`;
 const n=p.keywords?.length||0;
 $('pmRadarState').innerHTML=n?`<span class="pm-keywords-ready">已生成 / 保存 ${n} 条关键词</span> · ${p.active?'可用于手动采集':'尚未启用采集关键词'}`:'尚无美区检索词；上传资料并完成 GPT 分析后自动产生。';
}
function drawSources(){const ss=state.sources||[];$('pmSources').innerHTML=ss.length?ss.map(s=>`<label class="pm-source"><input type="checkbox" class="pm-source-check" value="${esc(s.id)}" checked><div><b>${esc(s.title)}</b><div class="pm-sub">${esc(({image:'原始图片',document:'产品文档',link:'商品链接',text:'人工文字'})[s.kind]||s.kind)} · ${s.text_length||0}字 · ${new Date(s.created_at*1000).toLocaleString()}</div>${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">查看原来源 ↗</a>`:''}<p>${esc(s.excerpt||s.note||'')}</p>${s.kind==='image'?`<button type="button" class="secondary pm-to-assets" data-sid="${esc(s.id)}">加入本地素材库</button>`:''}</div></label>`).join(''):'<div class="pm-sub">还没有资料。请先上传产品链接、说明书和实拍图片。</div>';
 $('pmSources').querySelectorAll('.pm-source-check').forEach(c=>c.addEventListener('change',dropPreview));
 $('pmSources').querySelectorAll('.pm-to-assets').forEach(b=>b.onclick=e=>{e.preventDefault();e.stopPropagation();work(async()=>{await api('/api/product-memory/image/to-assets',{product_id:pid,source_id:b.dataset.sid});notify('图片已加入视频素材库')})});
}
function drawMemory(){const m=state.memory||{},p=current();$('pmSummary').textContent=m.summary_cn||'等待上传资料并进行 GPT 商品理解。';
 const sections=[['适用消费人群',m.audiences_cn],['候选卖点（待验证）',m.selling_points_cn],['美区关键词建议',m.keyword_suggestions_en],['应排除的误导关键词',m.excluded_keywords_en],['风险与限制',m.risks_cn]];
 const priority=Array.isArray(m.selling_points_ranked)?m.selling_points_ranked:[];
 const groups=m.keyword_groups||{};
 const scored=priority.length?`<div class="pm-insight"><b>卖点优先级（GPT建议，人工事实审核优先）</b>${priority.map(v=>`<p><strong>${esc(v.priority||'P3')} · ${esc(v.point_cn)}</strong> <small>待核实 · ${esc(v.reason_cn||'')}</small></p>`).join('')}</div>`:'';
 const grouped=Object.keys(groups).some(k=>(groups[k]||[]).length)?`<div class="pm-insight"><b>关键词按用途分类（仅用于热点搜索，不证明商品功能）</b>${[['core','核心商品词'],['scenario','场景词'],['creative','创意词']].map(([key,label])=>`<p><strong>${label}</strong>：${(groups[key]||[]).map(esc).join(' / ')||'暂无'}</p>`).join('')}</div>`:'';
 $('pmInsights').innerHTML=scored+grouped+sections.filter(x=>Array.isArray(x[1])&&x[1].length).map(([name,a])=>`<div class="pm-insight"><b>${esc(name)}</b><p>${a.map(esc).join(' / ')}</p></div>`).join('');
 const facts=p?.facts||[];$('pmFactCards').innerHTML=facts.length?facts.map(f=>`<div class="pm-fact"><div><span class="pm-tag ${esc(f.status)}">${esc(({verified:'已确认',unverified:'待验证',prohibited:'禁止'})[f.status]||'待验证')}</span>${esc(f.text)}<small>${esc(f.source||'未注明来源')}</small></div><div class="pm-fact-actions">${['verified','prohibited','unverified'].map(v=>`<button type="button" class="secondary" data-fact="${esc(f.text)}" data-status="${v}" ${f.status===v?'disabled':''}>${({verified:'确认',prohibited:'禁止',unverified:'待验证'})[v]}</button>`).join('')}</div></div>`).join(''):'<div class="pm-sub">暂无经过分析的事实候选。</div>';
 $('pmFactCards').querySelectorAll('button[data-fact]').forEach(b=>b.onclick=()=>work(async()=>{await api('/api/product-memory/fact/status',{product_id:pid,text:b.dataset.fact,status:b.dataset.status});await reload(pid);notify('商品事实状态已更新')}));
 $('pmMemoryVersion').textContent=`商品知识版本 ${state.memory_version||0} · 已确认 ${facts.filter(x=>x.status==='verified').length} 条 · 待验证 ${facts.filter(x=>x.status==='unverified').length} 条`;
 drawWorkflow();
 $('pmHistory').innerHTML=(state.history||[]).slice(0,8).map(x=>`<div class="pm-sub">${new Date(x.created_at*1000).toLocaleString()} · ${esc(x.model)} · ${esc(x.status)} · 预估$${Number(x.estimated_usd||0).toFixed(4)} ${x.error?'·'+esc(x.error):''}</div>`).join('');
}

function drawWorkflow(){
 const p=current(),facts=p?.facts||[],approved=facts.filter(f=>f.status==='verified').length;
 const items=state?.radar_status||{};const hasMemory=Number(state?.memory_version||0)>0;
 const steps=[
  ['资料归档',(state?.sources||[]).length>0,`${(state?.sources||[]).length}份来源`],
  ['GPT 理解商品',hasMemory,hasMemory?`第${state.memory_version}版商品画像`:'尚未生成'],
  ['关键词生成',(p?.keywords||[]).length>0,`${(p?.keywords||[]).length}条`],
  ['人工核对关键事实',approved>0,`${approved}项已确认 · ${facts.filter(f=>f.status==='unverified').length}项待验证`],
  ['手动热点采集准备',(p?.keywords||[]).length>0&&!!p.active,p?.active?'关键词已就绪':'关键词尚未启用'],
  ['热点证据与带货脚本',Number(items.items||0)>0,`${items.items||0}条热点证据 · ${items.scripts||0}条已保存脚本`]
 ];
 const box=$('pmWorkflow');if(!box)return;
 box.innerHTML=steps.map((s,i)=>`<div class="pm-step ${s[1]?'pm-done':''}"><span>${s[1]?'✓':i+1}</span><div><b>${esc(s[0])}</b><small>${esc(s[2])}</small></div></div>`).join('');
 const unknown=(state.history||[]).find(h=>h.status==='submission_unknown');
 $('pmNextGuide').textContent=!hasMemory&&unknown?'商品分析曾超时、远端结果未知。请到任务中心核对 OpenLux 账单；不会自动重复扣费。':!hasMemory?'先完成 GPT 商品理解。':!((p?.keywords||[]).length)?'商品分析未提取可用关键词，请完善证据并重新分析。':!approved?'已生成关键词，请先核对商品事实，再前往手动采集热点。':!Number(items.items||0)?'已有关键词和已确认事实，可前往 Bright Data 手动采集热点。':'已获取热点证据，进入热点收集勾选来源，预检费用后生成双语脚本。';
 $('pmScanNow').disabled=!(p?.keywords||[]).length;
 $('pmViewRadar').disabled=!(p?.keywords||[]).length;
}
function drawOptions(){const pref=options?.product_model||'gpt-6.1-sol',sel=$('pmModel');if(Array.from(sel.options).some(o=>o.value===pref)){sel.value=pref;$('pmCustomModelWrap').hidden=true}else{sel.value='custom';$('pmCustomModelWrap').hidden=false;$('pmCustomModel').value=pref}
 $('pmDailyBudget').value=options?.daily_llm_usd_limit??0; refreshPrice();}
async function refreshPrice(force=false){
 const m=model();if(!m)return; const seq=++priceCheckSeq;
 $('pmPriceStatus').textContent='正在从 OpenLux 自动检查模型报价…';
 try{const p=await api('/api/model-pricing?model='+encodeURIComponent(m)+(force?'&refresh=1':''));
  if(model()!==m||seq!==priceCheckSeq)return;
  $('pmPriceStatus').textContent=p.available?`自动参考价：输入 $${p.input_usd_per_million}/百万词元 · 输出 $${p.output_usd_per_million}/百万词元 · ${p.source} · ${p.group_policy||''}`:(p.warning||'无法自动核实报价，已阻止付费分析');
 }catch(e){$('pmPriceStatus').textContent='价格检查失败：'+e.message}
}
async function reload(forcedId=null){
 const old=forcedId||pid;
 const newState=await api('/api/product-memory/state'+(old?'?product_id='+encodeURIComponent(old):''));
 const newOptions=(await api('/api/hot-radar/state')).options;
 state=newState;options=newOptions;pid=state.product_id||old||state.products[0]?.id||null;
 updateProductSelect();fillForm();drawSources();drawOptions();drawMemory();dropPreview();dirty=false;
}

function productPayload(){const p=current();if(!p)throw Error('请先新建商品');const name=$('pmName').value.trim()||(p.is_draft?p.name:'');return {id:pid,name,keywords:rows('pmKeywords'),facts:factLines(),restrictions:rows('pmRestrictions'),active:$('pmActive').checked};}
function canonicalProduct(p){
 const normFacts=f=>(f||[]).map(x=>[String(x.text||''),String(x.status||'unverified'),String(x.source||'')]);
 return {id:p.id,name:p.name,active:Boolean(p.active),keywords:(p.keywords||[]).map(String),
  facts:normFacts(p.facts),restrictions:(p.restrictions||[]).map(String)};
}
async function saveProduct(){
 const wanted=productPayload();
 const saved=await api('/api/hot-radar/product/save',wanted);
 let check;
 try{check=await api('/api/product-memory/state?product_id='+encodeURIComponent(saved.id))}
 catch(err){setFeedback('商品已写入数据库，但回读网络请求失败。请刷新查看：'+err.message,true);throw Error('数据库已返回保存成功，但回读失败；请勿重复新建商品')}
 const loaded=check.products?.find(x=>x.id===saved.id);
 if(!loaded||JSON.stringify(canonicalProduct(saved))!==JSON.stringify(canonicalProduct(loaded))){
  setFeedback('数据库已返回保存结果，但回读内容不同。保留当前编辑内容，请刷新后核对。',true);
  throw Error('保存已执行，但回读校验异常；请先核对现有商品，避免重复提交');
 }
 pid=saved.id;await reload(pid);dirty=false;
 const changes=[];
 if(wanted.keywords.length!==saved.keywords.length)changes.push('检索词按规则去重/截断');
 if((wanted.facts||[]).length!==(saved.facts||[]).length)changes.push('空白或超限事实已整理');
 const suffix=changes.length?'；'+changes.join('；'):'';
 setFeedback(`已保存：${saved.name} · ${new Date(saved.updated_at*1000).toLocaleString()} · ${saved.keywords.length}条关键词 · ${saved.facts.length}项事实${suffix}`);
 await reloadCatalog();window.HotRadar?.load();return saved;
}

async function sourceAction(fn){if(!pid)throw Error('请先点击「新建空白商品」');await fn();await reload(pid)}
['pmName','pmKeywords','pmFacts','pmRestrictions','pmActive'].forEach(id=>$(id)?.addEventListener('input',changed));
$('pmCatalogTab')?.addEventListener('click',()=>work(async()=>{if(dirty&&!confirm('有未保存修改，确定先进入商品档案吗？'))return;await reloadCatalog();switchView('catalog')}));
$('pmEditorTab')?.addEventListener('click',()=>{switchView('editor')});
$('pmCatalogNew')?.addEventListener('click',()=>{switchView('editor');$('pmNew')?.click()});
$('pmProduct')?.addEventListener('change',e=>work(async()=>{if(dirty&&!confirm('当前商品有未保存的改动，确定切换商品并放弃这些改动吗？')){$('pmProduct').value=pid;return}pid=e.target.value;await reload(pid);setFeedback('已切换商品，正在查看独立档案。')}));
$('pmNew')?.addEventListener('click',()=>work(async()=>{if(dirty&&!confirm('有未保存的改动，确定新建商品吗？'))return;const r=await api('/api/product-memory/new',{});pid=r.id;await reload(pid);$('pmUrl').value='';$('pmFiles').value='';$('pmText').value='';notify('已创建新的空白商品。可直接上传图片、链接或文档，无需先填关键词')}));
$('pmSave')?.addEventListener('click',()=>work(async()=>{await saveProduct();notify('商品名称与人工审核状态已保存')}));
$('pmSaveKeywords')?.addEventListener('click',()=>work(async()=>{await saveProduct();notify('热点检索词与跟踪设置已保存')}));
$('pmSaveAdvanced')?.addEventListener('click',()=>work(async()=>{await saveProduct();notify('商品事实与宣传限制已保存')}));
$('pmUrlAdd')?.addEventListener('click',()=>work(async()=>await sourceAction(async()=>{const u=$('pmUrl').value.trim();if(!u)throw Error('请输入有效的 HTTPS 商品链接');const r=await api('/api/product-memory/source/url',{product_id:pid,url:u});notify(r.duplicate?'链接已存在':'已提取网页资料；待 GPT 解读')})));
$('pmTextAdd')?.addEventListener('click',()=>work(async()=>await sourceAction(async()=>{await api('/api/product-memory/source/text',{product_id:pid,title:$('pmTextTitle').value,text:$('pmText').value});$('pmText').value='';notify('文字资料已保存')})));
$('pmFiles')?.addEventListener('change',()=>work(async()=>await sourceAction(async()=>{const files=Array.from($('pmFiles').files||[]);if(files.length>12)throw Error('每批最多12个文件');let count=0;for(const file of files){if(file.size>12*1024*1024)throw Error(`${file.name}超过12MB`);const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(Error('读取附件失败'));r.readAsDataURL(file)});await api('/api/product-memory/source/upload',{product_id:pid,name:file.name,data_base64:data});count++} $('pmFiles').value='';notify(`已归档 ${count} 个附件；下一步选择 GPT 进行商品理解`)})));
$('pmModel')?.addEventListener('change',()=>{$('pmCustomModelWrap').hidden=$('pmModel').value!=='custom';refreshPrice(true);dropPreview();$('pmModelStatus').textContent='已切换候选模型，请查询 OpenLux 目录并重新核验报价。'});
$('pmCustomModel')?.addEventListener('input',()=>{dropPreview();$('pmPriceStatus').textContent='请点击「确认并保存所选模型」或「自动核验报价」。';});
for(const id of ['pmCheckImages','pmDailyBudget'])$(id)?.addEventListener('input',dropPreview);
$('pmSaveModel')?.addEventListener('click',()=>work(async()=>{
 const chosen=model();if(!chosen)throw Error('请先选择模型');
 await api('/api/hot-radar/options/save',{product_model:chosen,daily_llm_usd_limit:Number($('pmDailyBudget').value||0)});
 const saved=(await api('/api/hot-radar/state')).options;
 if(saved.product_model!==chosen)throw Error('模型保存后回读校验失败');options=saved;
 $('pmModelStatus').textContent=`已保存且确认生效：${saved.product_model}`;
 await refreshPrice(true);dropPreview();notify('模型已保存并从数据库回读确认');
}));
$('pmCheckModel')?.addEventListener('click',()=>work(async()=>{
 const r=await api('/api/product-memory/models?model='+encodeURIComponent(model()));
 $('pmModelStatus').textContent=(r.available===true?'目录已收录：':r.available===false?'目录未找到：':'目录状态未知：')+r.message;
 await refreshPrice(true);
}));

async function runPreview(){
 if(!pid)throw Error('请先新建商品');
 if(dirty){await saveProduct()}
 const ids=[...document.querySelectorAll('.pm-source-check:checked')].map(x=>x.value);
 if(!ids.length)throw Error('请先上传至少一份产品链接、文档或图片；图片本身可以不含文字');
 const chosen=model();if(!chosen)throw Error('请先选择模型');
 const budget=Number($('pmDailyBudget').value||0);
 if(!Number.isFinite(budget)||budget<=0)throw Error('每日 GPT 预算目前为0。请设置正数（例如1美元），再预检；此操作尚未扣费');
 const opt=await api('/api/hot-radar/options/save',{product_model:chosen,daily_llm_usd_limit:budget});
 const saved=(await api('/api/hot-radar/state')).options;
 if(saved.product_model!==chosen||Number(saved.daily_llm_usd_limit)!==budget)throw Error('模型/预算保存后回读不一致');
 options=saved;
 const req={product_id:pid,model:chosen,source_ids:ids,analyze_images:$('pmCheckImages').checked};
 const p=await api('/api/product-memory/analyze/preview',req);preview={...p,req};
 const valid=Number.isFinite(p.estimated_cost_usd)&&p.estimated_cost_usd>0;
 $('pmEstimate').innerHTML=`模型：${esc(p.model)} · 资料：${p.source_count}项 · 图片：${p.images_to_analyze}张<br>预估输入 ${p.estimated_input_tokens}词元，输出上限 ${p.max_output_tokens}词元<br>预计费用：<b>${valid?'$'+p.estimated_cost_usd.toFixed(5):'报价不完整，不能扣费'}</b><br>${esc(p.warning)}`;
 setAnalysisGate(valid?'单次确认并用 GPT 分析（已完成预检）':'报价未核实，点击查看原因');
 if(!valid){setFeedback('无法核实模型费用：请先点击「自动核验 OpenLux 价格」或切换可用模型。不会发起付费请求。',true)}
 return valid;
}
$('pmPreview')?.addEventListener('click',()=>work(async()=>{const valid=await runPreview();if(valid){setFeedback('费用预检已完成：可以单次授权 GPT 分析。');notify('预检完成；尚未产生 GPT 费用')}}));
$('pmReparseAnalysis')?.addEventListener('click',()=>work(async()=>{await reload(pid);const task=(state.history||[]).find(x=>x.status==='parse_failed');if(!task)throw Error('当前商品没有可安全恢复的已保存原始结果（此功能不会重新调用 GPT）');const r=await api('/api/product-memory/analyze/reparse',{task_id:task.id});await reload(pid);setFeedback('恢复完成：商品知识第 '+r.memory_version+' 版，已从本地保存的旧模型响应恢复，无新增付费。')}));
$('pmRefreshAnalysisStatus')?.addEventListener('click',()=>work(async()=>{await reload(pid);const latest=(state.history||[])[0];if(!latest){setFeedback('尚无分析任务，未发起新请求');return}const m={running:'已提交后台单次分析，请等待响应；最长300秒（只读查询不重新扣费）',completed:'分析完成并已归档',submission_unknown:'远端结果未知：请在任务中心核账并解除阻塞；本系统不会自动重试或恢复无任务号的同步响应',reconciled_no_charge:'核对后未见扣费，可单次重新授权分析',reconciled_charged:'用户确认已扣费，原结果未恢复；如重新分析可能再次产生费用',rejected:'明确失败，未完成商品理解'};setFeedback(`最近任务 ${latest.id}：${m[latest.status]||latest.status}；${latest.error||''}` ,latest.status==='submission_unknown');}));
$('pmAnalyze')?.addEventListener('click',()=>work(async()=>{
 if(!preview){const valid=await runPreview();if(!valid)throw Error('模型报价尚未核实，已停止分析；无需重复点击')}
 if(!preview||!Number.isFinite(preview.estimated_cost_usd)||preview.estimated_cost_usd<=0)throw Error('尚未获得有效预估费用');
 const cost=preview.estimated_cost_usd,cap=+(cost*1.25).toFixed(7);
 if(!confirm(`确认单次调用 ${preview.model} 分析商品？\n预计 $${cost.toFixed(5)}；授权费用预检上限 $${cap.toFixed(5)}。\n如供应商实际计费高于预估，以账单为准；不会自动重试或自动生成视频。`))return;
 setAnalysisGate('正在分析商品，请勿重复点击…');$('pmAnalyze').disabled=true;
 try{
  const result=await api('/api/product-memory/analyze/generate',{...preview.req,request_hash:preview.request_hash,state_hash:preview.state_hash,approve_paid:true,max_cost_usd:cap});
  setFeedback(`已取得本地分析编号 ${result.id}；后台单次调用中，不要重新提交。可切换页面，返回后点击「查询最近分析状态」。`);
  notify('任务已安全入队；等待 OpenLux 返回结果。关闭网页不会取消后台请求。');
  const taskId=result.id;
  // Read-only polling. No duplicate paid request. Session browser may close safely.
  for(let i=0;i<90;i++){
    await new Promise(resolve=>setTimeout(resolve,4000));
    let status;
    try{status=await api('/api/product-memory/analyze/status?task_id='+encodeURIComponent(taskId))}
    catch(e){setFeedback('状态查询失败（不会重新扣费）：'+e.message,true);break}
    if(status.status==='running'){
      setFeedback(`GPT 商品理解：后台处理中 · 已运行 ${Math.max(0,Math.floor(Date.now()/1000-status.created_at))} 秒 · 任务 ${taskId}`);
      continue;
    }
    await reload(pid);window.HotRadar?.load();
    if(status.status==='completed'){
      clearChanged();setFeedback(`商品知识第 ${status.memory_version} 版保存成功。请到「04 商品记忆与事实审核」查看，并审核新提取的事实。`);
      $('pmAfterAnalysis')?.scrollIntoView({behavior:'smooth',block:'start'});
    }else{
      setFeedback(`任务 ${taskId} 的状态：${status.status}。${status.error||''}；不会自动重复付费。`,true);
    }
    break;
  }
 }finally{setAnalysisGate()}
}));
$('pmScanNow')?.addEventListener('click',()=>work(async()=>{
 if(dirty)await saveProduct();
 if(!(current()?.keywords||[]).length)throw Error('此商品还没有可用关键词，需先完成 GPT 分析或手动保存关键词');
 if(typeof activatePage==='function')activatePage('radar');
 await window.HotRadar?.selectProduct?.(pid);
 setFeedback('已进入手动热点收集。请先预览关键词与额度，确认后才会发起 Bright Data 付费采集。');
}));
$('pmViewRadar')?.addEventListener('click',()=>{
 if(dirty){notify('当前有未保存的商品修改，请先保存后进入热点雷达。',true);return}
 if(typeof activatePage==='function')activatePage('radar');window.HotRadar?.selectProduct?.(pid);
});
$('pmToVideo')?.addEventListener('click',()=>{const p=current();if(!p)return;const verified=(p.facts||[]).filter(x=>x.status==='verified');if(!verified.length){notify('请先确认商品事实，不能把未经核实的模型推测直接用于广告',true);return}const target=document.getElementById('videoPrompt');if(!target){notify('当前版本未找到视频提示词输入框',true);return}const t=`【商品已确认事实：${p.name}】\n${verified.map(x=>'• '+x.text).join('\n')}\n禁止宣传：${(p.restrictions||[]).join('；')}\n视频不得虚构商品功能或外观。`;target.value=[target.value,t].filter(Boolean).join('\n\n');target.dispatchEvent(new Event('input',{bubbles:true}));if(typeof activatePage==='function')activatePage('video');notify('已引用真实商品事实')});
$('pmToRadar')?.addEventListener('click',()=>{if(typeof activatePage==='function')activatePage('radar')});
window.ProductMemory={load:()=>work(async()=>{if(dirty){setFeedback('仍有未保存修改，未自动刷新表单，避免覆盖。');return}await reload(pid);await reloadCatalog();switchView(view)}),selectProduct:id=>work(async()=>{await reload(id);switchView('editor')})};
})();

// 用户可以在页面中下部直接保存，不必滚动回顶部。
document.getElementById('pmSaveBottom')?.addEventListener('click',()=>document.getElementById('pmSave')?.click());
