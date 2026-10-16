/* Seedance Studio 4.2 - visual composition only. No API calls or paid actions. */
(()=>{'use strict';
let initialized=false;
function elt(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;}
function v42Radar(){
 const page=document.querySelector('#page-radar');if(!page || page.dataset.v42Ready)return;
 const rows=page.querySelectorAll(':scope > .radar-columns');if(rows.length!==2)return;
 const [config,results]=rows;
 const [product,settings]=config.querySelectorAll(':scope > section');
 const [evidence,scripts]=results.querySelectorAll(':scope > section');
 if(!product||!settings||!evidence||!scripts)return;
 // Two vertical columns stack independently. No empty area beneath the short product card.
 const wrap=elt('div','v42-radar-workflow');
 const left=elt('div','v42-radar-primary');
 const right=elt('div','v42-radar-secondary');
 left.append(product,evidence);right.append(settings,scripts);wrap.append(left,right);
 config.replaceWith(wrap);results.remove();
 // Focus on real results; put outdated configuration controls in expandable details.
 const title=settings.querySelector('h3');
 if(title) title.textContent='02 · 分析设置';
 const details=elt('details','v42-radar-settings');
 const summary=elt('summary','','模型、预算及人工扫描参数');
 details.append(summary);
 // Keep source status and scan-status visible; other existing controls remain intact in details.
 const keep=new Set([title,settings.querySelector('#radarCapabilities'),settings.querySelector('#radarScanStatus'),settings.querySelector('#radarWindowStatus')].filter(Boolean));
 for(const child of Array.from(settings.children)){if(!keep.has(child))details.appendChild(child)}
 if(settings.querySelector('#radarCapabilities'))settings.querySelector('#radarCapabilities').after(details);
 else if(title)title.after(details);else settings.prepend(details);
 // Ensure status stays at the bottom of the panel and appears on failed collections.
 const status=settings.querySelector('#radarScanStatus');if(status)settings.append(status);
 const win=settings.querySelector('#radarWindowStatus');if(win)settings.append(win);
 // Legacy disabled schedule controls should not dominate UI, but remain in DOM for backend compatibility.
 for(const id of ['radarAutoScan','radarScanHour']){const input=settings.querySelector('#'+id);if(input){const label=input.closest('label');if(label)label.classList.add('v42-legacy-control')}}
 evidence.querySelector('h3')?.replaceChildren(document.createTextNode('03 · 热点证据'));
 scripts.querySelector('h3')?.replaceChildren(document.createTextNode('04 · GPT 创意脚本'));
 const panel=page.querySelector(':scope > .bd-panel');if(panel){panel.classList.add('v42-collection-panel')}
 page.dataset.v42Ready='1';
}
function v42Replica(){
 const page=document.querySelector('#page-replica');if(!page||page.dataset.v42Ready)return;
 const ref=page.querySelector('.replica-reference-v33'),command=page.querySelector('.replica-command-v33');
 if(!ref||!command)return;
 // Materials all together on the left: reference video + product photos + audio.
 const materials=elt('div','v42-replica-materials');
 const heading=elt('div','v42-subhead','补充参考素材');materials.append(heading);
 for(const cls of ['.product-inline-v33','#replicaProductList','.audio-replica-box','#replicaAudioList']){
   const node=command.querySelector(cls);if(node)materials.append(node);
 }
 const note=ref.querySelector('.reference-note-v33');
 if(note)note.before(materials);else ref.append(materials);
 // Secondary output parameters become optional disclosure. Keep actual task model visible.
 const optional=elt('details','v42-replica-extra');
 optional.append(elt('summary','','成片清晰度'));
 for(const id of ['replicaResolution']){
   const label=command.querySelector('#'+id)?.closest('label');if(label)optional.append(label);
 }
 const promptTitle=command.querySelector('.replica-instruction-title-v33');
 if(promptTitle)promptTitle.before(optional);else command.append(optional);
 const model=command.querySelector('.replica-model-line');
 const prompt=command.querySelector('#replicaInstruction');
 if(model&&prompt){prompt.after(model);model.classList.add('v42-replica-model')}
 ref.classList.add('v42-input-card');command.classList.add('v42-input-card');
 page.dataset.v42Ready='1';
}
function v42Settings(){
 const page=document.querySelector('#page-settings');if(!page||page.dataset.v42Ready)return;
 // Self-contained groups, no input IDs or form controls are removed.
 const sections=[...page.children].filter(el=>el.classList?.contains('grid')||el.classList?.contains('card'));
 for(const item of sections)item.classList.add('v42-settings-group');
 const banner=page.querySelector('.settings-banner');if(banner)banner.classList.add('v42-settings-banner');
 page.dataset.v42Ready='1';
}
function v42Ads(){
 const page=document.querySelector('#page-ads');if(!page||page.dataset.v42Ready)return;
 const cards=Array.from(page.querySelectorAll(':scope > .card'));
 if(cards.length>=3){const content=elt('div','v42-ads-content');content.append(cards[1],cards[2]);cards[0].after(content)}
 page.dataset.v42Ready='1';
}
function v42History(){
 const page=document.querySelector('#page-history');if(!page||page.dataset.v42Ready)return;
 page.querySelector('.grid.two')?.classList.add('v42-history-overview');
 page.dataset.v42Ready='1';
}
function v42Image(){
 const page=document.querySelector('#page-image');if(!page||page.dataset.v42Ready)return;
 const source=page.querySelector('#imageSourceWrap');const input=page.querySelector('.image-input-v33');
 if(source)source.classList.add('v42-image-source');
 if(input)input.classList.add('v42-image-control');
 page.dataset.v42Ready='1';
}
function v42Generic(){
 document.querySelector('#page-products')?.classList.add('v42-knowledge-page');
 document.querySelector('#page-video')?.classList.add('v42-video-page');
 document.querySelector('#page-assets')?.classList.add('v42-assets-page');
 document.querySelector('#page-assistant')?.classList.add('v42-chat-page');
 const h=document.querySelector('.topbar');if(h)h.classList.add('v42-topbar');
}
function run(){if(initialized)return;initialized=true;
 try{v42Generic();v42Radar();v42Replica();v42Settings();v42Ads();v42History();v42Image()}catch(e){console.warn('V4.2 layout enhancement skipped:',e)}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run,{once:true});else run();
})();
