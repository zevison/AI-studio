/* Product UI: textContent for untrusted filenames, Agent output and errors. */
(() => {
  const el = id => document.getElementById(id);
  let session, selectedProject, timer, busy = false;
  const active = new Set(['authoring', 'validating', 'building', 'exporting']);
  async function write(route, value) {
    if (!session) session = (await api('/api/hypit/session')).token;
    return api('/api/hypit/' + route, {method:'POST', headers:{'Content-Type':'application/json',
      'X-Hypit-Session':session}, body:JSON.stringify(value || {})});
  }
  function readableError(value) {
    const text=String(value || '');
    if (/Save API key|account read token|encrypted-store API key|required.*key/i.test(text))
      return '请先到“状态与设置”保存 API 密钥和账号只读令牌，再查询费用或启动 AI。';
    if (/quote.*expired|quote expired|Project or quote expired/i.test(text)) return '费用报价已过期或制作要求已变化，请重新查询并确认。';
    if (/budget|grant.*exhausted/i.test(text)) return '本次预算或尝试次数不足，任务已停止，不会自动追加费用。';
    if (/Project is busy|already.*running/i.test(text)) return '该任务正在处理，请等待完成后再操作。';
    if (/[\u4e00-\u9fff]/.test(text)) return text;
    return '操作未完成。请检查素材和设置后重试；详细原因可将鼠标停在这条提示上查看。';
  }
  function message(value, error=false) { el('hypitMessage').textContent = error ? readableError(value) : value; el('hypitMessage').title=error?String(value):''; }
  async function action(fn) {
    if (busy) return;
    busy = true;
    try { await fn(); } catch (error) { message(error.message,true); }
    finally { busy = false; }
  }
  function button(title, fn, disabled = false) {
    const result = document.createElement('button');
    result.type = 'button'; result.className = 'secondary'; result.textContent = title;
    result.disabled = disabled; result.onclick = () => action(fn); return result;
  }
  async function refreshProjects() {
    const rows = await api('/api/hypit/projects');
    el('hypitProjects').replaceChildren();
    if (!rows.length) { const empty=document.createElement('p');empty.className='hypit-empty';empty.textContent='还没有制作任务。添加素材并填写要求后，点击“保存制作任务”。';el('hypitProjects').append(empty); }
    for (const row of rows) {
      const card = document.createElement('article'); card.className = 'task';
      const title = document.createElement('h3'); title.textContent = row.title;
      const status = document.createElement('p');
      const names = {prepared:'下一步：开始 AI 编排', authoring:'AI 正在编排方案', ready:'方案已就绪，可以合成视频', awaiting_video_approval:'方案已就绪，请确认视频费用', needs_revision:'方案需要调整，可以让 AI 再试一次',
        building:'正在制作视频', exporting:'正在保存成片', completed:'制作完成', blocked:'任务已暂停，请检查原因', interrupted:'任务中断，等待核对'};
      status.className='hypit-task-note';
      status.textContent = (names[row.status] || '等待处理') + (row.error ? ' · ' + readableError(row.error) : '');
      if(row.error)status.title=row.error;
      const cost = document.createElement('p');
      cost.className='hypit-task-note';
      cost.textContent = `AI 已尝试 ${row.metadata.token_calls || 0} 次 · 编排费用估算 US$${row.metadata.token_estimate_usd || '0'}，实际以账号账单为准。`;
      const controls = document.createElement('div'); controls.className = 'hypit-actions';
      const advanced=document.createElement('details');advanced.className='hypit-details';
      const summary=document.createElement('summary');summary.textContent='高级操作：检查方案、编辑工程';advanced.append(summary);
      const extra=document.createElement('div');extra.className='hypit-actions';advanced.append(extra);
      controls.append(button(row.status==='prepared'?'开始 AI 编排':'让 AI 调整方案', async () => {
        if (!el('hypitConsent').checked) throw new Error('请先勾选第 3 步的编排费用授权');
        selectedProject = row.id;
        await write('author', {id:row.id, approve_token_charges:true,auto_build_local:true, limit_usd:el('hypitBudget').value,
          max_calls:Number(el('hypitCalls').value)});
        el('hypitConsent').checked = false; message('AI 已开始编排。可在下方查看进度，刷新页面不会停止任务。'); await refreshProjects();
      }, active.has(row.status) || ['blocked','interrupted'].includes(row.status)));
      extra.append(button('检查方案（免费）', async () => {
        const result = await write('validate', {id:row.id});
        message(result.ok ? '方案检查通过，可以合成视频。' : result.requires_video_approval ? '方案检查通过，请查询并确认视频费用。' : '方案尚有问题，可让 AI 调整后重试。');
        await refreshProjects();
      }, active.has(row.status)));
      if(['ready','completed'].includes(row.status))controls.append(button('合成视频（不调用生成模型）', async () => {
        await write('build', {id:row.id}); message('正在用现有素材合成视频。'); await refreshProjects();
      }, !['ready','completed'].includes(row.status)));
      const referenceConsent=document.createElement('input');referenceConsent.type='checkbox';
      const referenceLabel=document.createElement('label');referenceLabel.className='hypit-check';
      referenceLabel.append(referenceConsent,document.createTextNode('确认参考图片/视频不含真实人脸，并允许上传本任务所选素材，用于查询和生成视频。'));
      async function prepareVideoQuote(upload=false) {
        if(upload && !referenceConsent.checked)throw new Error('上传前请确认参考素材不含真实人脸及本次上传授权');
        const quote=await write('video-quote',{id:row.id,...(upload?{approve_reference_upload:true,confirm_no_real_person_faces:true}:{})});
        if(upload)referenceConsent.checked=false;
        const panel=document.createElement('div');
        const detail=document.createElement('p');
        detail.textContent=(quote.recovery_only?'复用已确认的原任务，不提交新的生成请求；原任务费用仍以账单为准。':
          `本次视频预计 US$${quote.estimated_usd}。报价有效期 5 分钟，修改方案后需要重新报价。供应商不提供费用硬封顶，实际以账单为准。`)+
          (quote.reference_count && !quote.reference_protocol_live_verified?'此参考模式仍属实验功能，请确认可以接受本次测试风险。':'');
        const consent=document.createElement('input');consent.type='checkbox';
        const label=document.createElement('label');label.className='hypit-check';
        label.append(consent,document.createTextNode('授权本工程最多 1 次生成，预算 US$3；提交不明时停止，不重发'+
          (quote.reference_count && !quote.reference_protocol_live_verified?'；我同意本次参考模式实测风险':'')));
        const generate=button('确认费用，生成视频',async()=>{
          if(!consent.checked)throw new Error('请确认这个工程的单次视频预算');
          await write('build-paid',{id:row.id,quote_id:quote.quote_id,approve_video_charges:true,limit_usd:'3.00',
            approve_unverified_reference_protocol:Boolean(quote.reference_count)});
          consent.checked=false;message('已启动视频制作。请在任务中查看进度。');await refreshProjects();
        });
        panel.append(detail);
        if(!quote.recovery_only)panel.append(label,generate);
        if(row.metadata.remote_video_task?.task_id)panel.append(button('仅恢复原任务，不追加生成',async()=>{
          await write('build-paid',{id:row.id,quote_id:quote.quote_id,approve_video_charges:true,limit_usd:quote.approved_limit_usd || '3.00',recover:true,
            approve_unverified_reference_protocol:Boolean(quote.reference_count)});
          message('正在查询、收集原任务；新生成已禁止');await refreshProjects();
        }));
        card.append(panel);
      }
      if(row.metadata.allow_generation_plan || row.metadata.remote_video_task?.task_id) {
        controls.append(button('查询视频费用',()=>prepareVideoQuote(),active.has(row.status)||row.status==='prepared'));
        controls.append(button('上传参考素材并查询费用',()=>prepareVideoQuote(true),active.has(row.status)||row.status==='prepared'));
        controls.append(referenceLabel);
      }
      extra.append(button('查看工程代码', async () => {
        selectedProject = row.id;
        el('hypitSource').textContent = (await api('/api/hypit/source?id=' + row.id + '&file=main.svml')).content;
        el('hypitSource').parentElement.open = true;
      }));
      if(!window.STUDIO_CONTEXT?.remote) extra.append(button('打开可视化编辑器', async () => {
        const result = await write('studio', {id:row.id});
        const link = document.createElement('a'); link.href = result.url;
        link.target = '_blank'; link.rel = 'noopener'; link.textContent = '点击打开可视化编辑器';
        el('hypitMessage').replaceChildren(link);
      }, active.has(row.status)));
      if (active.has(row.status)) controls.append(button('取消后续执行', async () => {
        await write('cancel', {id:row.id}); message('已请求取消；已提交请求不能撤回，不会自动追加调用');
      }));
      card.append(title,status,cost,controls,advanced);
      const result = row.metadata.result;
      if (result?.local_url) {
        const video = document.createElement('video'); video.controls = true;
        video.preload = 'metadata'; video.style.maxWidth = '100%'; video.src = result.local_url;
        const link = document.createElement('a'); link.href = result.local_url;
        link.textContent = '下载已归档成片'; link.download = ''; card.append(video,link);
      }
      el('hypitProjects').append(card);
    }
    clearTimeout(timer);
    if (rows.some(row => active.has(row.status))) timer = setTimeout(() => refreshProjects().catch(e => message(e.message,true)), 3000);
  }
  async function refresh() {
    // The existing HUD estimates the legacy video form, not this Agent project.
    // Hide it on this page; the project cards show their own token/build costs.
    document.getElementById('costHud').classList.toggle('hidden', el('page-hypit').classList.contains('active'));
    const health = await api('/api/hypit/health');
    el('hypitHealth').textContent = health.local_build && health.skill_installed ?
      '✓ 制作环境已就绪' : '制作环境不完整，请重新运行 01_INSTALL.cmd 安装程序';
    const rows = await api('/api/assets');
    const chosen=new Set([...el('hypitAssets').querySelectorAll('input:checked')].map(x=>x.value));
    el('hypitAssets').replaceChildren();
    for (const asset of rows) {
      const label = document.createElement('label'); label.className='hypit-check';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = asset.id;
      checkbox.dataset.kind = asset.kind;
      checkbox.checked=chosen.has(asset.id);
      checkbox.onchange = () => el('hypitAudioOptions').classList.toggle('hidden',
        !el('hypitAssets').querySelector('input[data-kind="audio"]:checked'));
      label.classList.add('hypit-asset-row');
      const detail=document.createElement('span');detail.className='hypit-asset-detail';
      const name=document.createElement('span');name.className='hypit-asset-name';
      name.textContent=asset.original_name || asset.stored_name || '未命名素材';name.title=name.textContent;
      const kind=document.createElement('span');kind.className='hypit-asset-kind';
      kind.textContent={video:'视频',image:'图片',audio:'音频'}[asset.kind] || '素材';
      detail.append(name,kind);label.append(checkbox,detail);
      const remove=document.createElement('button');remove.type='button';remove.className='hypit-asset-remove';remove.textContent='删除';remove.title='从可选素材中删除';remove.setAttribute('aria-label','删除素材 '+(asset.original_name||asset.id));
      remove.onclick=event=>{event.preventDefault();event.stopPropagation();action(async()=>{await window.removeStudioUpload(asset.id);await refresh();});};label.append(remove);el('hypitAssets').append(label);
    }
    el('hypitAudioOptions').classList.toggle('hidden', !el('hypitAssets').querySelector('input[data-kind="audio"]:checked'));
    await refreshProjects();
  }
  el('hypitRefresh').onclick = () => action(refresh);
  el('hypitCreate').onclick = () => action(async () => {
    const chosen = [...el('hypitAssets').querySelectorAll('input:checked')];
    if(!chosen.some(x=>x.dataset.kind==='video'))throw new Error('请在第 1 步选择至少一个参考视频。');
    if(!el('hypitObjective').value.trim())throw new Error('请在第 2 步填写制作要求。');
    const row = await write('create', {objective:el('hypitObjective').value, asset_ids:chosen.map(x => x.value),
      allow_generation_plan:el('hypitAllowGenerationPlan').checked,
      generation_task_type:el('hypitGenerationType').value,
      ...(chosen.some(x => x.dataset.kind === 'audio') ? {audio_mode:el('hypitAudioMode').value} : {})});
    selectedProject = row.id; message('任务已保存，尚未扣费。确认第 3 步预算后，在任务卡片中点击“开始 AI 编排”。'); await refreshProjects();
  });
  el('hypitQuote').onclick = () => action(async () => {
    const q = await write('quote');
    el('hypitQuoteResult').textContent = `${q.model}：输入 US$${q.input_usd_per_million}/百万 Token，输出 US$${q.output_usd_per_million}/百万 Token；采用当前账号最高适用分组报价。`;
  });
  el('hypitUpload').onchange = () => action(async () => {
    for (const file of el('hypitUpload').files) {
      const body = new FormData(); body.append('file', file);
      await api('/api/upload', {method:'POST',body});
    }
    el('hypitUpload').value = ''; await refresh();
  });
  window.HypitStudio = {load:() => action(refresh),open:async id => { document.querySelector('[data-page="hypit"]').click();
    selectedProject = id; await refreshProjects(); }};
  el('hypitAllowGenerationPlan').onchange=()=>{el('hypitGenerationOptions').hidden=!el('hypitAllowGenerationPlan').checked;};
  for (const nav of document.querySelectorAll('[data-page]')) nav.addEventListener('click', () => {
    document.getElementById('costHud').classList.toggle('hidden', nav.dataset.page === 'hypit');
  });
  if (el('page-hypit').classList.contains('active')) action(refresh);
})();
