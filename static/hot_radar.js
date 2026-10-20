/* V3.9.8.8 TikTok-only Radar UI: no unrelated data providers or automatic paid generation. */
(()=>{
 'use strict';
 const $r=id=>document.getElementById(id);
 const html=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const msg=(s,bad=false)=>{if(typeof toast==='function')toast(s,bad);else alert(s)};
 const apiR=async(path,body)=>{const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'请求失败');return j.data};
 let data=null, pid=null, selected=new Set(), preview=null, editingNew=false, brightJobs=[];
 const $val=id=>$r(id)?.value||'';
 const lists=id=>$val(id).split('\n').map(s=>s.trim()).filter(Boolean);
 const controlsDisabled=on=>{['radarSaveOptions','radarImportSubmit','radarBatchSubmit','radarPreviewScript','radarGenerateScript'].forEach(id=>{if($r(id))$r(id).disabled=on});if(!on&&$r('radarGenerateScript'))$r('radarGenerateScript').disabled=!(preview&&preview.estimated_cost_usd!=null)};
 function current(){return(data?.products||[]).find(p=>p.id===pid)}
 function productSelect(){const sel=$r('radarProduct'); if(!sel)return;sel.innerHTML=(data.products||[]).map(p=>`<option value="${html(p.id)}">${html(p.name)}</option>`).join('');
   if(!pid||!data.products.some(p=>p.id===pid))pid=data.products[0]?.id||null;
   sel.value=pid||'';window.ProductFlow?.set(pid);
   const missing=!(data.products||[]).length;
   if(missing)sel.innerHTML='<option value="">请先到商品知识中心创建商品</option>';
   sel.disabled=missing;
   if($r('bdSubmit'))$r('bdSubmit').disabled=missing;
   if($r('bdPreview'))$r('bdPreview').disabled=missing;
 }
 function showOpts(){const o=data.options;$r('radarAutoScan').checked=o.auto_scan;$r('radarScanHour').value=o.scan_hour_bj;$r('radarModel').value=o.script_model;
   $r('radarDailyBudget').value=o.daily_llm_usd_limit??0;refreshRadarPrice();}
 async function refreshRadarPrice(){const m=$val('radarModel').trim();if(!m)return;
 const status=$r('radarPriceStatus');if(!status)return;
 status.textContent='正在自动核对 OpenLux 价格…';
 try{const p=await apiR('/api/model-pricing?model='+encodeURIComponent(m));if($val('radarModel').trim()!==m)return;
 status.textContent=p.available?`输入 $${p.input_usd_per_million}/百万词元，输出 $${p.output_usd_per_million}/百万词元。${p.group_policy||''}`:(p.warning||'价格未核实；禁止生成');
 }catch(e){status.textContent='价格检查失败：'+e.message}}
 function renderCaps(){const c=data.capabilities||{};$r('radarCapabilities').innerHTML=`<p>${html(c.source_focus||'TikTok 视频人工复查')}</p><p>${html(c.tiktok_api||'仅手动采集')}</p>`}
 function renderStats(){const ir=(data.items||[]).filter(i=>i.product_id===pid && /^https:\/\/([^\/]+\.)?tiktok\.com\//i.test(i.url||''));const ss=(data.scripts||[]).filter(s=>s.product_id===pid);const latest=(data.runs||[]).find(r=>r.product_id===pid);const bt=(brightJobs||[]).find(j=>j.product_id===pid);const bl={submitting:'等待提交回执',running:'正在采集',ready:'正在读取结果',done:'已完成采集',failed:'采集失败',rejected:'请求被拒绝',submission_unknown:'提交状态待核实'};
   $r('radarStats').innerHTML=`<div><strong>${ir.length}</strong><span>已存热点证据</span></div><div><strong>${ir.filter(x=>x.kind==='video').length}</strong><span>视频候选</span></div><div><strong>${ss.filter(x=>x.status==='completed').length}</strong><span>已完成脚本</span></div><div><strong>${html(bt?(bl[bt.status]||bt.status):'未开始 Bright Data 采集')}</strong><span>最新采集状态</span></div>`;
   $r('radarScanStatus').textContent=bt?`Bright Data：${bl[bt.status]||bt.status} · 任务 ${bt.id} · 实际入库 ${bt.imported_records||0} 条${bt.error?' · '+bt.error.slice(0,180):''}`:(latest?`历史来源检查：${latest.status}`:'暂无采集任务');
   if($r('radarWindowStatus'))$r('radarWindowStatus').textContent='时效标签随当前日期重算：近7天优先、第8—14天补充，历史视频仅作参考。Bright Data 实际返回范围由服务商决定。';}
 let videoUploadTarget=null;
 function renderItems(){
   const items=(data.items||[]).filter(x=>x.product_id===pid && x.kind==='video' && /^https:\/\/([^\/]+\.)?tiktok\.com\//i.test(x.url||''));
   const box=$r('radarItems');
   if(!items.length){box.innerHTML='<div class="radar-empty">当前没有真实 TikTok 视频候选。请先手动采集 Bright Data 或导入 TikTok 视频。</div>';return}
   box.innerHTML=items.map(x=>{
     const md=x.metadata||{},vs=x.video_review||{},view=md.metrics?.viewCount;
     const poster=md.cover_url&&/^https:\/\//.test(md.cover_url)?`<img class="radar-cover" src="${html(md.cover_url)}" alt="视频封面" loading="lazy" referrerpolicy="no-referrer">`:'<div class="radar-cover radar-no-cover">视频</div>';
     const region=md.region||'待验证',fresh=md.freshness||'发布时间待核实';
     const regionNote=md.us_status==='非美国地区'?' · 非美区参考':md.us_status==='地区待验证'?' · 地区未核实':'';
     const dl=vs.download_status||'idle';
     const localDl=dl==='running'?' · 原帖下载进行中':dl==='failed'?' · 原帖下载失败':dl==='interrupted'?' · 下载中断':dl==='done'?' · 原帖已下载':'';
     const status=(vs.ready?'已关联真实视频 · '+(vs.duration?Number(vs.duration).toFixed(1)+'秒':'可解析'):'尚未获取真实视频')+localDl;
     return `<div class="radar-item viral-item ${selected.has(x.id)?'viral-selected':''}">${poster}<div class="viral-main">
       <div class="radar-item-head"><span class="radar-pill">${html(fresh)}</span><b>${html(x.title)}</b></div>
       <div class="radar-meta">${html(x.published_at||'日期待核验')} · 地区 ${html(region)}${html(regionNote)} · 播放 ${view!=null?Number(view).toLocaleString('en-US'):'未知'} · 点赞 ${html(md.metrics?.likeCount??'未知')}</div>
       <div class="radar-meta">${html(status)} · 不代表已验证成交</div>${vs.download_error?`<div class="radar-meta" style="color:var(--danger,#f99)">原帖下载提示：${html(vs.download_error)}</div>`:''}
       <div class="viral-actions"><a href="${html(x.url)}" target="_blank" rel="noopener noreferrer">查看原视频 ↗</a>
       <button class="secondary" type="button" data-viral-download="${html(x.id)}" ${md.media_url?'':'disabled title="原采集记录无视频媒体地址"'}>获取视频</button>
       <button class="secondary" type="button" data-viral-post="${html(x.id)}" ${dl==='running'?'disabled':''}>原帖自动获取</button>
       <button class="secondary" type="button" data-viral-browser="${html(x.id)}" ${dl==='running'?'disabled':''} title="需要明确许可使用本机浏览器会话，绝不上传浏览器Cookie">浏览器授权获取</button>
       <button class="secondary" type="button" data-viral-upload="${html(x.id)}">上传视频</button>
       <button class="${selected.has(x.id)?'primary':'secondary'}" type="button" data-viral-select="${html(x.id)}">${selected.has(x.id)?'取消选择':'选择拆解'}</button></div>
     </div></div>`
   }).join('');
   box.querySelectorAll('[data-viral-select]').forEach(b=>b.onclick=()=>{const id=b.dataset.viralSelect;selected=new Set(selected.has(id)?[]:[id]);renderItems();updateSelected()});
   box.querySelectorAll('[data-viral-post]').forEach(b=>b.onclick=()=>task(async()=>{
     if(!confirm('确认获取这一条公开TikTok原帖用于本地参考分析？\n不会再次调用Bright Data或OpenLux，不代表取得商业复制授权。'))return;
     const r=await apiR('/api/hot-radar/video/download-post',{item_id:b.dataset.viralPost,mode:'public',confirmed:true});
     msg(r.reused?'原帖下载任务已在运行':'已开始尝试获取原帖，任务在后台运行；页面刷新不会重新扣费。');await refresh();
   }));
   box.querySelectorAll('[data-viral-browser]').forEach(b=>b.onclick=()=>task(async()=>{
     const mode=prompt('仅当公开视频获取失败并且你有权限访问该视频时使用。\n输入 chrome 或 edge，取消即不执行：','chrome');
     if(mode===null)return;if(!['chrome','edge'].includes(mode.trim().toLowerCase()))throw Error('请选择 chrome 或 edge');
     if(!confirm('单次授权本机下载组件读取 '+mode+' 浏览器的当前登录会话以访问该原帖？\n本地工具不保存或上传 Cookie；读取可能失败或受浏览器/平台限制。'))return;
     await apiR('/api/hot-radar/video/download-post',{item_id:b.dataset.viralBrowser,mode:mode.trim().toLowerCase(),confirmed:true,browser_cookie_consent:true});
     msg('已开始本机浏览器会话下载，不涉及Bright Data或GPT付费调用');await refresh();
   }));
   box.querySelectorAll('[data-viral-upload]').forEach(b=>b.onclick=()=>{videoUploadTarget=b.dataset.viralUpload;$r('radarVideoFile')?.click()});
   box.querySelectorAll('[data-viral-download]').forEach(b=>b.onclick=()=>task(async()=>{
     if(!confirm('确认现在获取这条参考视频用于本机拆解？仅获取已收集的媒体地址，不会提交新的 Bright Data 采集，也不会调用 GPT。请确认你有权使用该视频。'))return;
     b.disabled=true;$r('radarScanStatus').textContent='正在获取真实视频并检查完整性…';
     try{await apiR('/api/hot-radar/video/fetch',{item_id:b.dataset.viralDownload,confirmed:true});await refresh();msg('视频已保存到本机，可人工选择拆解')}finally{b.disabled=false}
   }));
 }
 $r('radarVideoFile')?.addEventListener('change',async e=>{const file=e.target.files?.[0];const item_id=videoUploadTarget;e.target.value='';if(!file||!item_id)return;
   try{const fd=new FormData();fd.append('file',file,file.name);const r=await fetch('/api/upload',{method:'POST',body:fd});const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'上传失败');
     const asset=(j.data||[]).find(x=>x.kind==='video');if(!asset)throw Error('请上传MP4等视频文件');
     await apiR('/api/hot-radar/video/bind',{item_id,asset_id:asset.id});await refresh();msg('真实视频已关联到本条TikTok来源，现在可以选择拆解')
   }catch(err){msg(err.message,true)}finally{videoUploadTarget=null}
 });
 function updateSelected(){$r('radarSelected').textContent=`已选 ${selected.size} 条视频（一次只拆解一条；没有真实视频不可调用GPT）`;$r('radarGenerateScript').disabled=true;preview=null;$r('radarEstimate').textContent=''}
 function renderScripts(){const ss=(data.scripts||[]).filter(s=>s.product_id===pid);$r('radarScripts').innerHTML=ss.length?`<h4>历史创意脚本</h4>`+ss.slice(0,15).map(s=>{
     const d=s.content?.script||{};return `<div class="radar-script"><div class="radar-title-row"><b>${html(s.title||s.status)}</b><span>${html(s.status==='completed'?'已完成':'待核对')}</span></div>${s.status==='completed'?`<div class="radar-script-hook"><b>${html(d.hook_en||'')}</b><small>${html(d.hook_zh||'')}</small></div><details><summary>查看真实视频拆解与完整脚本</summary>${d.product_match?`<p><b>商品匹配：</b>${html(d.product_match.reason_cn||'待复核')}<br><small>采用的已核实声明：${html((d.product_match.verified_claims_used||[]).join('；'))}</small></p>`:''}${(d.video_breakdown||[]).map(x=>`<p><b>原视频 ${html(x.time_seconds??'?')}秒</b> · ${html(x.visual_cn||'')}<br><small>创意分析：${html(x.creative_insight_cn||'待人工复查')}</small></p>`).join('')}${(d.script_segments||[]).map(x=>`<p><b>${html(x.duration_s||'')}秒</b> ${html(x.narration_en)}<br><span>${html(x.narration_zh)}</span><br><small>画面：${html(x.visual_cn)}</small></p>`).join('')}<p>${html(d.reason_cn||'')}</p><p>注意：${html((d.risk_notes_cn||[]).join('；'))}</p></details><button class="secondary radar-copy" data-review-script="${html(s.id)}">编辑并确认脚本</button>`:`<p class="tiny muted">${html(s.error||'脚本调用待核对，请勿重复提交')}</p>`}<small class="muted">请求模型：${html(s.model)}${s.content?.actual_model?' · 实际返回：'+html(s.content.actual_model):' · 响应模型未返回'}${s.content?.model_mismatch?' · ⚠ 模型不一致':''} · 预计：${s.estimated_cost_usd==null?'未知':'$'+Number(s.estimated_cost_usd).toFixed(4)} · 实际扣费待账单核实</small></div>`
   }).join(''):'<div class="radar-empty">尚未生成脚本。先选定来源并预览费用。</div>';
   $r('radarScripts').querySelectorAll('[data-review-script]').forEach(b=>b.onclick=async()=>{try{
     const job=await apiR('/api/creative-production/new',{script_id:b.dataset.reviewScript});
     if(typeof activatePage==='function')activatePage('ads');
     if(window.CreativeProduction)window.CreativeProduction.load(job.id);
     msg('脚本已进入人工审核队列，尚未调用视频模型。');
   }catch(e){msg(e.message,true)}});
 }

 function rerender(){productSelect();showOpts();renderCaps();renderStats();renderItems();renderScripts();updateSelected();window.BrightTK?.refresh?.()}
 async function load(){try{pid=window.ProductFlow?.get()||pid;const fresh=window.ProductFlow?.begin('radar')||(()=>true);const result=await apiR('/api/hot-radar/state'+(pid?'?product_id='+encodeURIComponent(pid):''));if(!fresh())return;data=result;rerender()}catch(e){msg('热点雷达加载失败：'+e.message,true)}}
 setInterval(()=>{if(document.querySelector('#page-radar')?.classList.contains('active')&&data?.items?.some(x=>x.video_review?.download_status==='running')){
   const was=pid,fresh=window.ProductFlow?.begin('radar')||(()=>true);apiR('/api/hot-radar/state'+(was?'?product_id='+encodeURIComponent(was):'')).then(r=>{if(!fresh()||pid!==was)return;data=r;renderStats();renderItems();renderScripts();}).catch(()=>{});
 }},5000);
 async function refresh(){const was=pid;window.ProductFlow?.set(was);const fresh=window.ProductFlow?.begin('radar')||(()=>true);const result=await apiR('/api/hot-radar/state'+(was?'?product_id='+encodeURIComponent(was):''));if(!fresh())return;data=result;pid=was;productSelect();renderStats();renderItems();renderScripts();updateSelected()}
 async function task(fn){controlsDisabled(true);try{await fn()}catch(e){msg(e.message,true)}finally{controlsDisabled(false)}}
 $r('radarProduct')?.addEventListener('change',async e=>{pid=e.target.value;window.ProductFlow?.set(pid);selected.clear();editingNew=false;updateSelected();$r('radarItems').textContent='正在读取当前商品的视频…';$r('radarScripts').textContent='正在读取当前商品的脚本…';try{await refresh();window.BrightTK?.refresh?.()}catch(error){msg('切换商品失败：'+error.message,true)}});
 $r('radarGoProduct')?.addEventListener('click',()=>{window.ProductFlow?.set(pid);if(typeof activatePage==='function')activatePage('products')});
 $r('radarCheckPrice')?.addEventListener('click',()=>task(refreshRadarPrice));
 $r('radarSaveOptions')?.addEventListener('click',()=>task(async()=>{
 const intended={auto_scan:$r('radarAutoScan').checked,scan_hour_bj:Number($val('radarScanHour')),script_model:$val('radarModel').trim(),daily_llm_usd_limit:Number($val('radarDailyBudget'))};
 await apiR('/api/hot-radar/options/save',intended);
 data=await apiR('/api/hot-radar/state'+(pid?'?product_id='+encodeURIComponent(pid):''));
 for(const k of Object.keys(intended))if(data.options[k]!==intended[k])throw Error('设置已提交但回读不一致：'+k);
 showOpts();msg('已保存，并从数据库回读验证生效');
 }));
 $r('radarCheckModels')?.addEventListener('click',async()=>{
  const el=$r('radarModelCatalog');el.textContent='正在只读查询OpenLux模型目录（不会生成视频或执行GPT分析）…';
  try{const result=await apiR('/api/hot-radar/vision-models');el.textContent=result.models.map(m=>(m.listed?'✓ ':'· ')+m.name+' ['+m.id+']').join(' / ')+'。'+result.note}
  catch(e){el.textContent='模型目录检查未成功：'+e.message}
 });
 $r('radarScanNow')?.addEventListener('click',()=>task(async()=>{if(!pid)throw Error('请先保存商品');$r('radarScanStatus').textContent='正在检查 TikTok 来源与发布时间（不扣费）…';const r=await apiR('/api/hot-radar/scan',{product_id:pid});await refresh();msg(`TikTok 来源检查：符合时间范围 ${r.discovered} 条，相关候选 ${r.quality_candidates||0} 条，${String(r.window?.mode||'').includes('14d')?'回看至14天':'优先近7天'}；${r.auto_tiktok_source_connected?'已连接':'未连接自动TikTok采集接口'}`)}));
 $r('radarImportSubmit')?.addEventListener('click',()=>task(async()=>{await apiR('/api/hot-radar/item/import',{product_id:pid,kind:$val('radarImportKind'),url:$val('radarImportUrl'),title:$val('radarImportTitle'),summary:$val('radarImportSummary')});await refresh();msg('已保存 TikTok 原始来源；热度和实际销量未验证')}));
 $r('radarBatchSubmit')?.addEventListener('click',()=>task(async()=>{
   if(!pid)throw Error('请先选择商品');
   let rows;try{rows=JSON.parse($val('radarBatchJson'))}catch{throw Error('需要 JSON 数组格式的 TikTok 导入记录')}
   const ret=await apiR('/api/hot-radar/tiktok/batch',{product_id:pid,records:rows});
   await refresh();msg(`导入 ${ret.inserted} 条，跳过 ${ret.skipped.length} 条；所有指标仍需核实`);
 }));
 $r('radarPreviewScript')?.addEventListener('click',()=>task(async()=>{if(selected.size!==1)throw Error('请先选择一条已绑定真实视频的候选');if(Number($val('radarDailyBudget'))<=0)throw Error('请先展开「GPT 模型、费用和预算设置」，设置每日 GPT 预算并保存');const model=$val('radarModel').trim(),p=await apiR('/api/hot-radar/scripts/preview',{product_id:pid,source_ids:[...selected],model});preview=p;$r('radarEstimate').innerHTML=`<b>拟调用模型：</b>${html(p.model)}<br><b>来源：</b>${p.source_count} 条<br><b>预计输入词元：</b>${p.estimated_input_tokens}<br><b>允许输出词元：</b>${p.max_output_tokens}<br><b>预估费用：</b>${p.estimated_cost_usd==null?'OpenLux 报价未核实，禁止调用':'$'+Number(p.estimated_cost_usd).toFixed(5)}<br><small>${html(p.warning)}</small>${p.evidence_warnings?.length?'<br><b>证据注意：</b>'+p.evidence_warnings.map(html).join('；').slice(0,600):''}`;$r('radarGenerateScript').disabled=p.estimated_cost_usd==null;msg('已生成费用预检，没有调用 GPT')}));
 $r('radarGenerateScript')?.addEventListener('click',()=>task(async()=>{if(!preview)throw Error('请先预检费用');const max=Number(preview.estimated_cost_usd)*1.25;if(!Number.isFinite(max)||max<=0)throw Error('费用无效');if(!confirm(`确认单次付费调用 ${preview.model} 生成脚本？\n预估 $${preview.estimated_cost_usd.toFixed(5)}，本次授权上限 $${max.toFixed(5)}。\n报价由 OpenLux 自动核验，最终实际扣费以账号账单为准。`))return;
   const r=await apiR('/api/hot-radar/scripts/generate',{product_id:pid,source_ids:[...selected],model:preview.model,preview_hash:preview.preview_hash,approve_paid:true,max_cost_usd:max});await refresh();msg(r.status==='completed'?`真实视频拆解和脚本已保存：${r.id}。点击「编辑并确认脚本」继续，实际账单待核验。`:`本次模型结果状态：${r.status}。请先复核，暂不能进入制作。`,r.status!=='completed')}));
 ['radarModel','radarDailyBudget'].forEach(id=>$r(id)?.addEventListener('input',()=>{updateSelected();if(id==='radarModel')$r('radarPriceStatus').textContent='模型已修改，保存后自动核验报价。'}));
 window.HotRadar={load,updateHarvest:(jobs)=>{brightJobs=Array.isArray(jobs)?jobs:[];if(data)renderStats()},selectProduct:async productId=>{pid=productId;window.ProductFlow?.set(pid);selected.clear();await refresh();renderStats();renderItems();renderScripts();window.BrightTK?.refresh?.()}};
})();
