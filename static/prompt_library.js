/* Visual prompt library: additive UI; does not replace or intercept OpenLux paid calls. */
(() => {
'use strict';
const root=document.getElementById('vpMount');if(!root)return;

const q=s=>root.querySelector(s);
const h=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const toastLocal=(s,err=false)=>{if(typeof toast==='function')toast(s,err);else alert(s)};
const pl={items:[],selected:null,visible:40,pendingImport:null,loaded:false};
async function call(url,data){
  const opts=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};
  const r=await fetch(url,opts);let d={};try{d=await r.json()}catch(e){}
  if(!r.ok||!d.ok)throw Error(d.error||`请求失败 ${r.status}`);
  return d.data;
}
async function refresh(pick){
  try{pl.items=await call('/api/prompt-library');pl.loaded=true;q('#vpCount').textContent=`${pl.items.length} 条`;
    const cat=q('#vpCategory'),previous=cat.value;const cats=[...new Set(pl.items.map(x=>x.category))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    cat.innerHTML='<option value="">全部分类</option>'+cats.map(x=>`<option value="${h(x)}">${h(x)}</option>`).join('');cat.value=cats.includes(previous)?previous:'';
    const code=pick||pl.selected?.code||pl.items[0]?.code;pl.selected=pl.items.find(x=>x.code===code)||null;render();
  }catch(e){q('#vpCount').textContent='未连接';q('#vpList').textContent=e.message;toastLocal(e.message,true)}
}
function filtered(){
  const key=q('#vpSearch').value.toLowerCase().trim(),cat=q('#vpCategory').value,fav=q('#vpFavoritesOnly').checked;
  return pl.items.filter(x=>(!cat||x.category===cat)&&(!fav||x.favorite)&&(!key||[x.name,x.code,x.category,x.description,x.prompt,x.tags].some(v=>String(v||'').toLowerCase().includes(key))));
}
function render(){
  const rows=filtered(),shown=rows.slice(0,pl.visible);
  q('#vpShown').textContent=`匹配 ${rows.length} 条`;
  q('#vpMore').classList.toggle('hidden',rows.length<=pl.visible);
  q('#vpList').innerHTML=shown.map(x=>`<button type="button" role="listitem" class="vp-item ${x.code===pl.selected?.code?'active':''}" data-vp-code="${h(x.code)}"><span class="vp-item-top"><span class="vp-item-title">${h(x.name)}</span><span class="vp-item-fav">${x.favorite?'★':''}</span></span><span class="vp-item-code">${h(x.code)} · ${h(x.category)}</span><span class="vp-item-sub">${h(x.description).slice(0,70)}</span></button>`).join('')||'<p class="vp-muted">没有匹配项，可以新建或导入指令。</p>';
  root.querySelectorAll('[data-vp-code]').forEach(b=>b.onclick=()=>{pl.selected=pl.items.find(x=>x.code===b.dataset.vpCode);render()});
  const x=pl.selected;q('#vpSelectedName').textContent=x?.name||'从左侧选择一个模板';q('#vpSelectedCode').textContent=x?.code||'';
  q('#vpSelectedCategory').textContent=x?.category||'选择指令';q('#vpSelectedDesc').textContent=x?.description||'选中后将展开为完整的可执行提示词。';q('#vpPromptView').value=x?.prompt||'';
  ['#vpApply','#vpCopy','#vpEdit','#vpDelete','#vpFavorite'].forEach(s=>q(s).disabled=!x);
  q('#vpFavorite').textContent=x?.favorite?'★ 已收藏':'☆ 收藏';
}
function expanded(x,extra=''){
  const ratio=document.querySelector('#imageRatio')?.value||'auto';
  return [x.prompt,ratio==='auto'?'画面比例：按照当前生成设置自动适配。':`画面比例：${ratio}。`,extra.trim()?`本次额外要求：${extra.trim()}`:''].filter(Boolean).join('\n\n');
}
function applyPrompt(x){
  const target=document.querySelector('#imagePrompt');if(!target||!x){toastLocal('图片生成输入框不存在，请先刷新页面',true);return;}
  const extra=q('#vpExtra').value||'';const content=expanded(x,extra);
  // Directly populate existing app's input; actual model request remains the existing pathway.
  target.value=content;
  target.dispatchEvent(new Event('input',{bubbles:true}));
  if(x.mode==='image'&&typeof setImageMode==='function'){
    setImageMode('image');
    if(typeof state!=='undefined'&&!state.imageSourceAssets?.length)toastLocal('已填入完整提示词。该模板建议上传真实产品图片后再生成。');
    else toastLocal('完整指令已填入图生图');
  }else toastLocal('完整指令已填入图片生成，可继续修改后生成');
  q('#vpPanel').classList.add('hidden');q('#vpToggle').textContent='打开指令库 ▾';q('#vpToggle').setAttribute('aria-expanded','false');
  target.focus();
}
function openFromSidebar(){
 const imageNav=document.querySelector('.nav[data-page="image"]');
 if(imageNav && !document.getElementById('page-image')?.classList.contains('active'))imageNav.click();
 if(q('#vpPanel').classList.contains('hidden'))q('#vpToggle').click();
 root.scrollIntoView({behavior:'smooth',block:'start'});
}

q('#vpToggle').onclick=()=>{const willOpen=q('#vpPanel').classList.contains('hidden');q('#vpPanel').classList.toggle('hidden',!willOpen);q('#vpToggle').textContent=willOpen?'收起指令库 ▴':'打开指令库 ▾';q('#vpToggle').setAttribute('aria-expanded',String(willOpen));if(willOpen&&!pl.loaded)refresh()};
let searchTimer=null;
q('#vpSearch').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{pl.visible=40;render()},140)});
q('#vpCategory').onchange=()=>{pl.visible=40;render()};q('#vpFavoritesOnly').onchange=()=>{pl.visible=40;render()};q('#vpMore').onclick=()=>{pl.visible+=50;render()};
q('#vpApply').onclick=()=>applyPrompt(pl.selected);
q('#vpCopy').onclick=async()=>{const x=pl.selected;if(!x)return;try{await navigator.clipboard.writeText(expanded(x,q('#vpExtra').value));toastLocal('完整提示词已复制')}catch(e){toastLocal('浏览器复制失败，请手动复制文本',true)}};
q('#vpFavorite').onclick=async()=>{const x=pl.selected;if(!x)return;try{await call('/api/prompt-library/favorite',{code:x.code,value:!x.favorite});await refresh(x.code)}catch(e){toastLocal(e.message,true)}};
function editorOpen(x){q('#vpEditorDetails').open=true;q('#vpEditCode').value=x?.code||'';q('#vpEditCode').readOnly=!!x;
  q('#vpEditName').value=x?.name||'';q('#vpEditCategory').value=x?.category||'个人导入';q('#vpEditDesc').value=x?.description||'';
  q('#vpEditPrompt').value=x?.prompt||'';q('#vpEditMode').value=x?.mode||'image';
  q('#vpEditorDetails').scrollIntoView({behavior:'smooth',block:'nearest'});q('#vpEditName').focus();
}
q('#vpNew').onclick=()=>editorOpen(null);q('#vpEdit').onclick=()=>editorOpen(pl.selected);
q('#vpCancelEdit').onclick=()=>{q('#vpEditorDetails').open=false};
q('#vpSave').onclick=async()=>{const row={code:q('#vpEditCode').value,name:q('#vpEditName').value,category:q('#vpEditCategory').value,description:q('#vpEditDesc').value,prompt:q('#vpEditPrompt').value,mode:q('#vpEditMode').value};
 try{const result=await call('/api/prompt-library/save',row);q('#vpEditorDetails').open=false;await refresh(result.code);toastLocal('指令已保存到本地数据库')}catch(e){toastLocal(e.message,true)}
};
q('#vpDelete').onclick=async()=>{const x=pl.selected;if(!x||!confirm(`确定从指令库隐藏 ${x.code}（${x.name}）吗？已生成的历史图片不受影响。`))return;
 try{await call('/api/prompt-library/delete',{code:x.code});pl.selected=null;await refresh();toastLocal('指令已隐藏')}catch(e){toastLocal(e.message,true)}
};
const importFormat=q('#vpImportFormat'),importText=q('#vpImportText'),conflict=q('#vpConflict');
function resetPreview(){pl.pendingImport=null;q('#vpConfirmImport').disabled=true;q('#vpImportFeedback').textContent='内容或处理方式已变化，请重新预览。'}
importText.addEventListener('input',resetPreview);importFormat.addEventListener('change',resetPreview);conflict.addEventListener('change',resetPreview);
q('#vpImportFile').addEventListener('change',async e=>{const file=e.target.files?.[0];if(!file)return;
 if(file.size>3*1024*1024){toastLocal('导入文件超过 3MB',true);return}
 const ext=file.name.split('.').pop().toLowerCase();if(!['json','csv','txt'].includes(ext)){toastLocal('请选择 JSON、CSV 或 TXT 文件',true);return}
 importFormat.value=ext;importText.value=await file.text();q('#vpImportFilename').textContent=`${file.name}（${Math.ceil(file.size/1024)} KB）`;resetPreview();
});
q('#vpPreviewImport').onclick=async()=>{
 try{const body={raw:importText.value,format:importFormat.value,on_conflict:conflict.value,preview:true};
   const result=await call('/api/prompt-library/import',body);
   pl.pendingImport={raw:body.raw,format:body.format,on_conflict:body.on_conflict};q('#vpConfirmImport').disabled=false;
   q('#vpImportFeedback').textContent=`检查通过：共 ${result.total} 条；新增 ${result.new} 条；已有重复 ${result.duplicate} 条。\n本次预计新增 ${result.would_insert} 条、覆盖 ${result.would_overwrite} 条、跳过 ${result.would_skip} 条。\n预览：${result.examples.map(x=>x.code+' '+x.name).join('；')||'空文件'}\n确认后才写入本地指令库。`;
 }catch(e){resetPreview();q('#vpImportFeedback').textContent='导入检查失败：'+e.message;toastLocal(e.message,true)}
};
q('#vpConfirmImport').onclick=async()=>{const p=pl.pendingImport;if(!p)return;
 if(p.on_conflict==='overwrite'&&!confirm('将覆盖现有同名快捷码的提示词。确认继续吗？'))return;
 q('#vpConfirmImport').disabled=true;
 try{const r=await call('/api/prompt-library/import',{...p,preview:false});pl.pendingImport=null;
   q('#vpImportFeedback').textContent=`完成：成功新增 ${r.inserted} 条，覆盖 ${r.overwritten} 条，跳过 ${r.skipped} 条。`;
   await refresh();toastLocal(`已导入 ${r.inserted+r.overwritten} 条指令`)
 }catch(e){toastLocal(e.message,true);q('#vpImportFeedback').textContent='导入失败：'+e.message}
};
function downloadText(content,filename,type){const obj=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=obj;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(obj),1200)}
q('#vpDownloadTemplate').onclick=()=>{const o={format:'seedance-prompt-library-v1',prompts:[{code:'/myhero',name:'自定义产品主图',category:'产品主图',description:'上传产品图生成主图',prompt:'以用户上传的真实产品图为依据，生成纯白背景商业主图，保持外观、颜色、商标一致。',mode:'image',tags:'产品,广告',source:'用户自定义',compatibility:'待实际出图验证'}]};downloadText(JSON.stringify(o,null,2),'指令库_JSON导入示例.json','application/json')};
async function exportFile(format){try{const r=await fetch('/api/prompt-library/export?format='+encodeURIComponent(format));if(!r.ok)throw Error('无法导出：'+r.status);const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`Seedance_视觉指令库_${new Date().toISOString().slice(0,10)}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1400)}catch(e){toastLocal(e.message,true)}}
q('#vpExportJson').onclick=()=>exportFile('json');q('#vpExportCsv').onclick=()=>exportFile('csv');
// Users may type /blueprint directly in the original image prompt field: expand it on click.
// Capture runs before the original click handler; no extra paid request is sent.
const generateBtn=document.getElementById('generateImage');
if(generateBtn)generateBtn.addEventListener('click',event=>{
 const input=document.getElementById('imagePrompt');if(!input||!pl.loaded)return;
 const value=input.value.trim();const match=value.match(/^(\/[a-z][a-z0-9_-]{1,47})(?=\s|$)/i);if(!match)return;
 const row=pl.items.find(x=>x.code===match[1].toLowerCase());if(!row)return;
 const remainder=value.slice(match[1].length).trim();input.value=expanded(row,remainder);
 input.dispatchEvent(new Event('input',{bubbles:true}));
 if(row.mode==='image'&&typeof state!=='undefined'&&!state.deepMode&&state.imageMode==='text'){
   // Stop generation: user must see that a reference image is needed, avoiding a paid prompt-only generation.
   event.stopImmediatePropagation();event.preventDefault();
   if(typeof setImageMode==='function')setImageMode('image');
   toastLocal('指令已展开。先上传真实产品图，再点击生成，避免无参考图误扣费。');
 }
},true);
refresh();
})();
