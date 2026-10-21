/* V3.9.4 reusable local @audio references for Video + Replica; paid endpoints unchanged. */
(()=>{
 'use strict';
 const $=s=>document.querySelector(s), modal=$('#audioRefBackdrop');if(!modal)return;
 const form={scope:'video',asset:null,length:0,waveform:null};
 const E=(s)=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
 function msg(x,error=false){const t=$('#audioRefMessage');t.textContent=x||'';t.style.color=error?'#ffaaa8':'#bbd1f2'}
 function clipTime(){return {start:Number($('#audioRefStart').value||0),end:Number($('#audioRefEnd').value||0)}}
 function setTab(saved){$('#audioRefNew').classList.toggle('hidden',saved);$('#audioRefSaved').classList.toggle('hidden',!saved);$('#audioRefTabNew').classList.toggle('active',!saved);$('#audioRefTabSaved').classList.toggle('active',saved);if(saved)loadSubjects()}
 function close(){modal.classList.add('hidden');modal.setAttribute('aria-hidden','true');$('#audioRefPlayer').pause();msg('')}
 function open(scope){form.scope=scope;form.asset=null;form.length=0;$('#audioRefFile').value='';$('#audioRefPlayer').removeAttribute('src');$('#audioRefPlayer').load();$('#audioRefName').value='';$('#audioRefDescription').value='';$('#audioRefSourceName').textContent='支持 WAV、MP3、M4A 等音频格式';$('#audioRefStart').value='0';$('#audioRefEnd').value='5';form.waveform=null;drawWave();setTab(false);modal.classList.remove('hidden');modal.setAttribute('aria-hidden','false');msg('选择音频后可以试听与裁剪。')}
 function attach(scope,asset){if(!asset||asset.kind!=='audio')throw Error('不是音频素材');
   const all=scope==='video'?state.videoRefs:state.replicaAudios;
   if(all.some(x=>x.id===asset.id)){msg('此音频已添加到当前任务。');return}
   const seconds=Number(asset.duration||0);const current=(scope==='video'?state.videoRefs:state.replicaAudios).filter(x=>x.kind==='audio');
   if(!seconds)throw Error('无法识别这段音频的时长，请先安装 FFmpeg');
   if(current.length>=10)throw Error('最多添加10个参考音频');
   if(current.reduce((v,x)=>v+Number(x.duration||0),0)+seconds>30.5)throw Error('参考音频总长不能超过30秒，请缩短片段');
   if(scope==='video'&&all.length>=50)throw Error('视频创作最多50个参考素材');
   const before=typeof mentionScopeAssets==='function'?mentionScopeAssets(scope):[];
   all.push(asset);renderAssets();
   if(scope==='video')invalidatePreviews();else invalidateReplicaPreparation('新音频已添加，请重新分析并准备生成。');
   const recs=buildAliasRecords(mentionScopeAssets(scope),scope);const ref=recs.find(x=>x.id===asset.id);const input=scope==='video'?$('#videoPrompt'):$('#replicaInstruction');
   // The reference choice controls the real output. Never auto-write instructions
   // that secretly force the original audio content into a timbre-only request.
   if(typeof scheduleDraftSave==='function')scheduleDraftSave();close();if(typeof toast==='function')toast('已添加 '+(ref?.__alias||'音频')+'；可在音频使用方式中选择音色参考或原音频内容。');
 }
 function drawWave(){const c=$('#audioRefWave');if(!c)return;const ctx=c.getContext('2d');if(!ctx)return;
   const W=c.width,H=c.height;ctx.clearRect(0,0,W,H);ctx.fillStyle='#11192a';ctx.fillRect(0,0,W,H);
   const dat=form.waveform;if(!dat){ctx.fillStyle='#7086ae';ctx.font='13px sans-serif';ctx.fillText('上传文件后显示音频波形',16,H/2);return}
   ctx.fillStyle='#94b9f5';for(let i=0;i<W;i+=3){const a=dat[i]||.03;const h=Math.max(2,a*(H-12));ctx.fillRect(i,(H-h)/2,2,h)}
   const {start,end}=clipTime();const total=form.length||1;ctx.fillStyle='rgba(155,110,244,.18)';ctx.fillRect(0,0,W*start/total,H);ctx.fillRect(W*end/total,0,W-W*end/total,H);
   ctx.fillStyle='#c9aaff';ctx.fillRect(W*start/total,0,2,H);ctx.fillRect(W*end/total,0,2,H);
 }
 async function loadWave(asset){form.waveform=null;drawWave();try{
   const response=await fetch(asset.url);if(!response.ok||Number(response.headers.get('content-length')||0)>35e6)return;
   const bytes=await response.arrayBuffer();if(bytes.byteLength>35e6)return;
   const context=new(window.AudioContext||window.webkitAudioContext)();try{
     const buf=await context.decodeAudioData(bytes);const data=buf.getChannelData(0),step=Math.max(1,Math.floor(data.length/700));const arr=[];
     for(let i=0;i<700;i++){let max=0;const st=i*step;for(let j=st;j<Math.min(data.length,st+step);j+=Math.max(1,Math.floor(step/40)))max=Math.max(max,Math.abs(data[j]));arr.push(max)}
     if(form.asset?.id===asset.id){form.waveform=arr;drawWave()}
   }finally{await context.close()}
 }catch(e){console.info('音频波形不可用，仍可用播放器试听',e)}}
 function showUploaded(scope,asset){form.scope=scope;form.asset=asset;form.length=Number(asset.duration||0);$('#audioRefSourceName').textContent=asset.original_name||'新音频';$('#audioRefPlayer').src=asset.url;$('#audioRefName').value=(asset.original_name||'音频参考').replace(/\.[^.]+$/,'').slice(0,40);$('#audioRefDescription').value='';$('#audioRefStart').value='0';$('#audioRefEnd').value=(form.length>0?Math.min(5,form.length):5).toFixed(1);modal.classList.remove('hidden');modal.setAttribute('aria-hidden','false');setTab(false);msg('可以拖动波形标记或填写起止秒数，点击试听片段。');loadWave(asset)}
 $('#addVideoAudio')?.addEventListener('click',()=>open('video'));$('#addReplicaAudio')?.addEventListener('click',()=>open('replica'));
 $('#audioRefClose').onclick=close;modal.addEventListener('click',e=>{if(e.target===modal)close()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!modal.classList.contains('hidden'))close()});
 $('#audioRefTabNew').onclick=()=>setTab(false);$('#audioRefTabSaved').onclick=()=>setTab(true);
 $('#audioRefFile').onchange=async e=>{const file=e.target.files?.[0];if(!file)return;msg('正在读取音频……');try{if(file.size>150*1024*1024)throw Error('音频文件过大，请裁剪后上传');if(!file.type.startsWith('audio/')&&!/\.(mp3|wav|m4a|flac|ogg|aac)$/i.test(file.name))throw Error('请选择有效的音频文件');const a=await uploadFileList([file]);const asset=a.find(x=>x.kind==='audio');if(!asset)throw Error('上传没有识别为音频');showUploaded(form.scope,asset)}catch(err){msg(err.message,true)}};
 $('#audioRefPlayer').addEventListener('loadedmetadata',()=>{const sec=$('#audioRefPlayer').duration;if(Number.isFinite(sec)&&sec>0){form.length=sec;$('#audioRefEnd').value=Math.min(Number($('#audioRefEnd').value||5),sec).toFixed(1);drawWave()}});
 $('#audioRefPlayer').addEventListener('timeupdate',()=>{if($('#audioRefPlayer').currentTime>=clipTime().end-0.025)$('#audioRefPlayer').pause()});
 ['#audioRefStart','#audioRefEnd'].forEach(sel=>$(sel).addEventListener('input',drawWave));
 $('#audioRefWave').addEventListener('click',e=>{if(!form.length)return;const c=e.currentTarget,r=c.getBoundingClientRect(),sec=Math.max(0,Math.min(form.length,(e.clientX-r.left)/r.width*form.length));const t=clipTime();const target=Math.abs(sec-t.start)<Math.abs(sec-t.end)?'#audioRefStart':'#audioRefEnd';$(target).value=sec.toFixed(1);drawWave()});
 $('#audioRefPlayClip').onclick=async()=>{const p=$('#audioRefPlayer'),t=clipTime();if(!form.asset)return msg('请先上传音频',true);if(t.end<=t.start)return msg('结束时间必须大于开始时间',true);try{p.currentTime=t.start;await p.play()}catch(e){msg('浏览器未能播放此音频，请尝试 WAV 或 MP3。',true)}};
 $('#audioRefSave').onclick=async()=>{const btn=$('#audioRefSave');if(btn.disabled)return;try{
   if(!form.asset)throw Error('先选择音频文件');const t=clipTime();if(!$('#audioRefName').value.trim())throw Error('请输入音频主体名称');if(t.start<0||t.end<=t.start||t.end>form.length+.25||t.end-t.start>.1+30)throw Error('裁剪范围无效，音频片段最长30秒');
   btn.disabled=true;msg('正在本地裁剪并保存，不调用付费模型……');const result=await post('/api/audio/subject/create',{asset_id:form.asset.id,name:$('#audioRefName').value.trim(),description:$('#audioRefDescription').value,start:t.start,end:t.end});
   attach(form.scope,result.asset);
 }catch(err){msg(err.message,true)}finally{btn.disabled=false}};
 async function loadSubjects(){const list=$('#audioRefSavedList');list.innerHTML='<p class="tiny muted">正在读取本地音频库…</p>';try{
   const rows=await api('/api/audio/subjects');list.innerHTML=(rows||[]).filter(x=>x.asset?.kind==='audio').map(x=>`<div class="audio-ref-subject"><div><b>${E(x.name)}</b><small>${E(x.description||x.asset.original_name||'')} · ${Number(x.asset.duration||0).toFixed(1)}s</small><audio controls preload="none" src="${E(x.asset.url)}"></audio></div><button type="button" class="secondary" data-audio-subject="${E(x.id)}">添加</button></div>`).join('')||'<p class="tiny muted">没有已保存的音频主体，可先从本地导入。</p>';
   list.querySelectorAll('[data-audio-subject]').forEach(btn=>btn.onclick=()=>{try{const x=rows.find(v=>v.id===btn.dataset.audioSubject);if(x)attach(form.scope,x.asset)}catch(err){msg(err.message,true)}});
 }catch(err){list.textContent='加载失败：'+err.message}}
 window.SeedanceAudioRef={open,showUploaded};
})();
