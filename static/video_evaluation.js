/* Free local checks and explicit human review; no generation endpoints. */
(()=>{'use strict';
const page=document.getElementById('page-ads');if(!page)return;
const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e};
const section=node('section');section.className='card radar-panel video-evaluation';section.setAttribute('aria-label','效果与成本验收');
section.append(node('h3','04 · 效果与成本验收'),node('p','这里免费读取已有成片、运行本地检查和保存人工评分，不会生成视频。生成成功不等于广告可用；实际账单不完整时不计算成本排名。'));
section.append(node('p','费用口径：这里只统计视频生成费，暂不包含脚本／AI编排、存储和人工成本。退款及账单尚未对齐时，先完成核对再用于预算。'));
const protocol=node('details'),summary=node('summary','查看对照测试方法与评分标准'),instructions=node('div');protocol.append(summary,instructions);section.append(protocol);
const toolbar=node('div');toolbar.className='evaluation-toolbar';const label=node('label','选择已有视频任务');const select=node('select');select.setAttribute('aria-label','选择效果验收任务');label.append(select);
const refresh=node('button','刷新记录（不重发任务）'),check=node('button','本地技术检查（免费）');refresh.type=check.type='button';toolbar.append(label,refresh,check);section.append(toolbar);
const state=node('p');state.className='evaluation-state';state.setAttribute('role','status');const video=node('video');video.controls=true;video.preload='metadata';video.hidden=true;section.append(state,video);
const sourceDetails=node('details'),sourceText=node('pre'),references=node('div');sourceDetails.append(node('summary','本次要求与参考素材（评分前核对）'),sourceText,references);section.append(sourceDetails);
const rubric=node('div');rubric.className='evaluation-rubric';section.append(rubric);
function checkbox(text){const label=node('label'),input=node('input');label.className='evaluation-check check';input.type='checkbox';label.append(input,node('span',text));section.append(label);return input}
const facts=checkbox('已核对商品外观、功能和宣传事实，没有新增或夸大卖点');
const usable=checkbox('本条已达到实际可用要求（每项≥3分、平均≥4分，且技术检查完成）');
const notesLabel=node('label','评审依据／退回修改原因'),notes=node('textarea');notes.rows=2;notes.maxLength=1000;notesLabel.append(notes);section.append(notesLabel);
const save=node('button','保存人工评审（免费）');save.type='button';section.append(save,node('h4','同条件效果与成本记录'));
const results=node('div');results.className='evaluation-results';section.append(results);page.append(section);
let data=null,pending=false,loaded=false,sequence=0;const scoreInputs={};
const current=()=>data?.tasks.find(t=>t.id===select.value);
const statuses={passed:'基础技术检查通过',warning:'技术检查有警告，须人工复核',failed:'基础技术检查失败',incomplete:'技术检查未完成',not_checked:'尚未检查'};
function lock(value){pending=value;for(const b of [refresh,check,save,select])b.disabled=value;}
async function request(path,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),180000);try{const response=await fetch('/api/video-evaluation/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:controller.signal,cache:'no-store'});const result=await response.json();if(!response.ok||!result.ok)throw Error(result.error||'读取失败');return result.data}finally{clearTimeout(timer)}}
function showTask(){const task=current();for(const score of Object.values(scoreInputs)){score.value='';score.disabled=!task||task.status!=='completed'||!task.output_url;}facts.disabled=usable.disabled=!task||task.status!=='completed'||!task.output_url;facts.checked=usable.checked=false;notes.value='';video.hidden=!task?.output_url;if(task?.output_url)video.src=task.output_url;else video.removeAttribute('src');
 sourceText.textContent=task?.instruction||'旧任务未记录原始要求，不能把缺失证据当作已核验。';references.replaceChildren();
 for(const ref of task?.references||[]){const a=node('a',ref.name||'参考素材');a.href=ref.url;a.target='_blank';a.rel='noopener';references.append(a,document.createTextNode(' '))}
 if(!task){state.textContent='暂无视频任务；先完成一次创作，再到这里评审。';check.disabled=save.disabled=true;return}
 check.disabled=save.disabled=task.status!=='completed'||!task.output_url;
 state.textContent=[statuses[task.technical_status]||'尚未检查',task.specification,task.local_only?'本地合成任务，不参与生成模型比较':'同条件组 '+(task.case_key||'规格不足，暂不比较'),!task.output_url?'当前空间未找到成片文件，请先恢复下载或重新本地导出，不要重新付费生成':'',task.review_stale?'成片或任务已变化，旧评审不再计入':'',task.cost_status==='verified_record'?'存在已记录的实际费用':'实际费用待账单核对'].filter(Boolean).join(' · ');
 if(task.review){for(const [key,value] of Object.entries(task.review.scores||{}))if(scoreInputs[key])scoreInputs[key].value=String(value);facts.checked=task.review.facts_verified===true;usable.checked=task.review.usable===true;notes.value=task.review.notes||'';}
}
function table(){results.replaceChildren();if(!data.comparisons.length){results.append(node('p','暂无可比较的真实生成记录；本地合成和规格缺失的旧任务不参与排名。'));return}
 const t=node('table'),head=node('tr');for(const text of ['模型／路由','同条件组','次数','可用率／95%区间','总视频生成费','视频生成费／合格片','证据'])head.append(node('th',text));t.append(head);
 const money=v=>v===null?'待核对':'US$'+Number(v).toFixed(4);
 for(const row of data.comparisons){const tr=node('tr');const rate=row.unsettled_attempts?'有'+row.unsettled_attempts+'条结果待核对':row.usable_rate===null?'待完成评审':(row.usable_rate*100).toFixed(0)+'%'+(row.usable_rate_95_interval?' / '+row.usable_rate_95_interval.map(v=>(v*100).toFixed(0)+'%').join('–'):'');
  for(const value of [row.model,row.case_key,row.attempts,rate,money(row.total_actual_usd),money(row.cost_per_usable_video_usd),row.evidence_level==='pilot'?'先导样本，不能定优劣':'描述性统计，不能保证效果'])tr.append(node('td',String(value)));t.append(tr)}results.append(t,node('p','最多读取最近200个视频任务。同组才可对照；计入失败尝试的实际费用。缺少评审或账单时，不把未知值当零，也不自动切换模型。'));
}
async function load(){const version=++sequence;let failure=null;lock(true);state.textContent='正在读取本地验收记录…';try{const next=await request('overview');if(version!==sequence)return;const previous=select.value;data=next;select.replaceChildren();for(const task of data.tasks){const option=node('option',(task.instruction.slice(0,28)||(task.local_only?'本地合成':'视频任务'))+' · '+({completed:'已完成',failed:'失败',running:'进行中'}[task.status]||task.status)+' · '+task.id.slice(-6));option.value=task.id;select.append(option)}if([...select.options].some(o=>o.value===previous))select.value=previous;
 if(!Object.keys(scoreInputs).length)for(const [key,title] of Object.entries(data.dimensions)){const label=node('label',title),input=node('select');input.setAttribute('aria-label',title+'评分');const empty=node('option','请选择评分');empty.value='';input.append(empty);for(let v=1;v<=5;v++){const option=node('option',String(v));option.value=String(v);input.append(option)}label.append(input);rubric.append(label);scoreInputs[key]=input}
 instructions.replaceChildren();const list=node('ol');for(const step of data.protocol.steps)list.append(node('li',step));instructions.append(list,node('p',data.protocol.score_anchors),node('p',data.protocol.reference_scope));table();loaded=true;
 }catch(error){failure=error.name==='AbortError'?'读取超时。刷新记录即可，不会生成视频。':error.message;}finally{lock(false);if(data)showTask();if(failure)state.textContent=failure}}
async function action(fn){if(pending)return;lock(true);let error=null;try{await fn()}catch(e){error=e.name==='AbortError'?'本地操作超时，先刷新记录核对；不会重发生成任务。':e.message}finally{lock(false);const t=current();check.disabled=save.disabled=!t||t.status!=='completed'||!t.output_url}if(error)state.textContent=error;}
refresh.onclick=()=>{if(!pending)load()};select.onchange=showTask;
check.onclick=()=>action(async()=>{const task=current();if(!task)return;state.textContent='正在检查本地视频；不调用生成模型…';await request('check',{task_id:task.id,identity:task.identity});await load()});
save.onclick=()=>action(async()=>{const task=current();if(!task)return;await request('review',{task_id:task.id,identity:task.identity,scores:Object.fromEntries(Object.entries(scoreInputs).map(([k,v])=>[k,Number(v.value)])),facts_verified:facts.checked,usable:usable.checked,notes:notes.value});await load();state.textContent='已保存人工评审。'+state.textContent;});
new MutationObserver(()=>{if(page.classList.contains('active')&&!loaded&&!pending)load()}).observe(page,{attributes:true,attributeFilter:['class']});
if(page.classList.contains('active'))load();
})();
