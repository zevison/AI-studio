/* Local maintenance only. No account credentials or arbitrary installer paths. */
(() => {
  'use strict';
  function node(tag,text) {
    const result=document.createElement(tag);
    if(text!==undefined)result.textContent=text;
    return result;
  }
  async function api(path,body) {
    const response=await fetch(path,{method:body?'POST':'GET',cache:'no-store',
      headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    const value=await response.json();
    if(!response.ok||!value.ok)throw new Error(value.error||'应用维护请求失败');
    return value.data;
  }
  function components(card) {
    const open=node('button','插件与组件');open.type='button';open.style.marginLeft='10px';
    const dialog=node('dialog');dialog.setAttribute('aria-label','插件与组件');
    dialog.style.cssText='width:min(700px,90vw);max-height:80vh;overflow:auto;padding:24px;border:1px solid #8290a5;border-radius:16px;background:var(--bg,#f6f7fb);color:var(--text,#172337)';
    const close=node('button','关闭');close.type='button';close.style.float='right';
    const list=node('div'),message=node('p'),preferences=node('div');message.setAttribute('role','status');
    const auto=node('input');auto.type='checkbox';auto.setAttribute('aria-label','允许已授权组件在使用时自动下载');
    const resume=node('input');resume.type='checkbox';resume.setAttribute('aria-label','重新打开后恢复已授权的中断下载');
    const autoLabel=node('label');autoLabel.append(auto,node('span','允许已授权组件在使用时自动下载'));
    const resumeLabel=node('label');resumeLabel.append(resume,node('span','重新打开后恢复已授权的中断下载'));
    preferences.append(autoLabel,node('br'),resumeLabel);
    const sourceOrder=node('select');sourceOrder.setAttribute('aria-label','组件下载来源优先级');
    for(const [value,text] of [['domestic_first','国内优先（推荐）'],['official_first','官方优先（排错）']]){const option=node('option',text);option.value=value;sourceOrder.append(option);}
    sourceOrder.addEventListener('change',async()=>{try{render(await api('/api/components/preferences',{source_order:sourceOrder.value}));}catch(error){message.textContent=error.message;}});
    preferences.append(node('br'),sourceOrder);
    for(const input of [auto,resume])input.addEventListener('change',async()=>{
      try{render(await api('/api/components/preferences',{automatic_on_demand:auto.checked,resume_interrupted:resume.checked}));}
      catch(error){message.textContent=error.message;await poll();}
    });
    const log=node('button','查看组件日志');log.type='button';
    const logText=node('pre');logText.hidden=true;logText.style.cssText='white-space:pre-wrap;max-height:220px;overflow:auto';
    log.addEventListener('click',async()=>{try{logText.textContent=(await api('/api/components/logs')).text;logText.hidden=false;}catch(error){message.textContent=error.message;}});
    const clearCache=node('button','清理下载缓存');clearCache.type='button';
    clearCache.addEventListener('click',async()=>{
      if(!window.confirm('只清理已下载的组件缓存、断点及中断的临时安装目录，不删除已安装组件、素材、配置或任务历史。后续修复或导出离线包可能需要重新下载。确认清理？'))return;
      try{const result=await api('/api/components/clear-cache',{confirmed:true});message.textContent=result.note+' · 已释放 '+(result.freed_bytes/1048576).toFixed(1)+' MB';}
      catch(error){message.textContent=error.message;}
    });
    // Keep the chooser connected while polling replaces component rows. Removing a
    // file input with its native picker open can discard the selected file.
    const offlineFile=node('input');offlineFile.type='file';offlineFile.accept='.zip';offlineFile.hidden=true;
    offlineFile.setAttribute('aria-label','离线组件 ZIP 文件');
    dialog.append(close,node('h3','插件管理'),node('p','首次使用某项功能时才安装对应组件。确认后在后台下载，不影响主界面；断点及已校验的缓存会保留。'),preferences,list,message,log,clearCache,logText,offlineFile);
    let busy=false,pending=false,importBusy=false,importTarget=null;
    const manualExpanded=new Set();
    offlineFile.addEventListener('cancel',()=>{importTarget=null;offlineFile.value='';});
    offlineFile.addEventListener('change',async()=>{
      const file=offlineFile.files?.[0],row=importTarget;
      offlineFile.value='';importTarget=null;
      if(!file||!row||importBusy)return;
      const raw=row.raw===true;
      if(!window.confirm('导入 '+row.title+(raw?' 的原始下载文件':' 的离线组件包')+'？程序将按本版本受信任清单核对身份、大小及 SHA-256。全部文件齐全后检测并安装；不会自动转为联网下载。'))return;
      importBusy=true;await poll();message.textContent='正在传入离线组件包…';
      let importError='';
      try{
        const response=await fetch('/api/components/'+(raw?'import-file':'import')+'?id='+encodeURIComponent(row.id),{method:'POST',
          headers:{'Content-Type':raw?'application/octet-stream':'application/zip','X-Component-Confirm':'1'},body:file});
        const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'离线包导入失败');render(result.data);
      }catch(error){importError=error.message;}
      finally{importBusy=false;await poll();if(importError)message.textContent=importError;}
    });
    const labels={not_installed:'未安装',queued:'等待下载',receiving:'接收离线包',downloading:'下载中',verifying:'正在校验',installing:'正在安装',install_failed:'安装失败',health_check:'检测运行能力',ready:'可使用',installed:'已安装，待本次校验',needs_repair:'需要修复',pausing:'正在暂停',paused:'已暂停',failed:'下载失败'};
    function render(value) {
      const pluginJob=value.plugin_updates?.job||{};
      busy=importBusy||Boolean(value.active)||pluginJob.state==='running';list.replaceChildren();
      clearCache.disabled=busy||value.can_manage===false;
      open.textContent=value.active?'插件与组件 · 下载中':pluginJob.state==='running'?'插件与组件 · 检查更新中':'插件与组件';
      auto.checked=Boolean(value.preferences?.automatic_on_demand);resume.checked=Boolean(value.preferences?.resume_interrupted);
      const readOnly=value.can_manage===false;
      auto.disabled=resume.disabled=readOnly;
      sourceOrder.value=value.preferences?.source_order||'domestic_first';sourceOrder.disabled=busy||readOnly;
      if(!value.components.length)list.append(node('p','此版本所需组件已随完整安装包提供。'));
      for(const row of value.components) {
        const section=node('section');section.style.cssText='padding:16px 0;border-bottom:1px solid #8290a544';
        section.append(node('h4',row.title+' · '+(labels[row.state]||'待下载')));
        section.append(node('p','所需版本：'+(row.display_version||row.version)+(row.current_version?' · 已安装：'+(row.current_display_version||row.current_version):'')));
        section.append(node('p',row.purpose||'本地运行组件'));
        if(row.installed_bytes)section.append(node('p','下载 '+(row.total/1048576).toFixed(1)+' MB · 安装占用 '+(row.installed_bytes/1048576).toFixed(1)+' MB'));
        if(row.dependencies?.length){
          const names={'core.node':'程序运行时','core.ffmpeg':'视频处理工具','core.hypit-sdk':'官方 Hypit 工作流','core.asr-engine':'本地语音引擎'};
          section.append(node('p','已内置依赖：'+row.dependencies.map(id=>names[id]||id).join('、')));
        }
        if(row.reviewed_sources?.length)section.append(node('p','经核验来源：'+row.reviewed_sources.join(' → ')));
        if(row.manual_files?.length){
          const detail=node('details');detail.open=manualExpanded.has(row.id);
          detail.append(node('summary','下载失败？打开备用下载网址／手动导入'));
          detail.addEventListener('toggle',()=>{if(detail.open)manualExpanded.add(row.id);else manualExpanded.delete(row.id);});
          detail.append(node('p','请逐个下载下列固定版本文件，再点“导入原始下载文件”。不要运行下载文件，不要下载“最新版”。链接是否可访问取决于当前网络；导入后仍会校验，齐全后才安装。'));
          for(const file of row.manual_files){
            detail.append(node('strong',file.name+(file.cached?' · 已缓存（安装前复核）':'')));
            const checksum=node('p','大小：'+file.bytes+' 字节 · SHA-256：'+file.sha256);
            checksum.style.cssText='overflow-wrap:anywhere;font-size:12px';detail.append(checksum);
            for(const url of file.urls||[]){
              // Only plain HTTPS addresses emitted by the embedded catalog.
              if(!/^https:\/\/[A-Za-z0-9.-]+\//.test(url))continue;
              const link=node('a',url);link.href=url;link.target='_blank';link.rel='noopener noreferrer';
              link.style.cssText='display:block;overflow-wrap:anywhere;margin:6px 0';detail.append(link);
            }
          }
          const rawImport=node('button','导入原始下载文件');rawImport.type='button';rawImport.disabled=busy||readOnly;
          rawImport.addEventListener('click',()=>{
            if(busy||readOnly)return;
            importTarget={id:row.id,title:row.title,raw:true};offlineFile.accept='';offlineFile.value='';offlineFile.click();
          });detail.append(rawImport);section.append(detail);
        }
        if(row.state!=='ready') {
          const progress=node('progress');progress.max=row.total;progress.value=row.downloaded||0;progress.style.width='100%';
          progress.setAttribute('aria-label',row.title+'下载进度');section.append(progress);
          section.append(node('p',((row.downloaded||0)/1048576).toFixed(1)+' / '+(row.total/1048576).toFixed(1)+' MB'+(row.source?' · 来源：'+row.source:'')));
          if(row.bytes_per_second>0)section.append(node('p',(row.bytes_per_second/1048576).toFixed(1)+' MB/s'+(row.eta_seconds!==null?' · 预计剩余 '+row.eta_seconds+' 秒':'')));
          const button=node('button',value.active===row.id?'暂停':row.state==='paused'?'继续下载':row.state==='failed'?'重试下载':'下载组件');button.type='button';
          button.disabled=readOnly||pending||(busy&&value.active!==row.id);
          button.addEventListener('click',async()=>{
            if(pending)return;pending=true;button.disabled=true;
            const installing=value.active!==row.id;
            if(installing&&!row.approved&&!window.confirm('首次使用此功能，需要安装运行组件。\n'+row.title+'\n版本：'+(row.display_version||row.version)+'\n下载大小：'+(row.total/1048576).toFixed(1)+' MB\n安装占用：'+((row.installed_bytes||row.total)/1048576).toFixed(1)+' MB\n用途：'+(row.purpose||'本地组件')+'\n优先使用已验证的国内源，SHA-256 校验通过后安装。确认在后台下载？')) {
              pending=false;button.disabled=false;return;
            }
            try{render(await api(value.active===row.id?'/api/components/cancel':'/api/components/download',
              installing?{id:row.id,confirmed:true}:{id:row.id}));}
            catch(error){message.textContent=error.message;}
            finally{pending=false;await poll();}
          });section.append(button);
        }
        const offline=node('button','导入离线组件包');offline.type='button';offline.disabled=busy||readOnly;
        offline.addEventListener('click',()=>{
          if(busy||readOnly)return;
          importTarget={id:row.id,title:row.title};offlineFile.accept='.zip';offlineFile.value='';offlineFile.click();
        });section.append(offline);
        const checkPlugin=node('button','检查插件更新');checkPlugin.type='button';
        checkPlugin.disabled=busy||readOnly||value.plugin_updates?.state!=='ready';
        checkPlugin.title=value.plugin_updates?.reason||'验证独立插件的 TUF 签名与兼容性，不会自动安装';
        checkPlugin.addEventListener('click',async()=>{
          checkPlugin.disabled=true;
          try{render(await api('/api/components/check',{id:row.id}));}catch(error){message.textContent=error.message;await poll();}
        });section.append(checkPlugin);
        if(pluginJob.id===row.id&&pluginJob.state==='update_available') {
          section.append(node('p','已验证更新：'+pluginJob.version+' · 下载 '+(pluginJob.download_bytes/1048576).toFixed(1)+' MB · '+(pluginJob.notes||'')));
          const installPlugin=node('button','更新组件');installPlugin.type='button';installPlugin.disabled=busy||readOnly;
          installPlugin.addEventListener('click',async()=>{
            if(!window.confirm('更新 '+row.title+' 到 '+pluginJob.version+'？下载 '+(pluginJob.download_bytes/1048576).toFixed(1)+' MB，安装占用 '+(pluginJob.installed_bytes/1048576).toFixed(1)+' MB。签名、兼容性和真实运行检查通过后切换；旧版及用户数据保留。'))return;
            try{render(await api('/api/components/update',{id:row.id,confirmed:true}));}catch(error){message.textContent=error.message;}
          });section.append(installPlugin);
        }
        if(pluginJob.id===row.id&&pluginJob.state==='up_to_date')section.append(node('p','已通过可信签名清单核对，当前插件没有版本变化。'));
        if(pluginJob.id===row.id&&pluginJob.state==='failed')section.append(node('p',pluginJob.error));
        for(const [label,action,explanation] of [
          ['校验并修复','repair','重新校验并检测组件；需要修复时会优先复用本地缓存，必要时从已验证来源下载。确认继续？'],
          ['移除组件','uninstall','移除当前组件并撤销自动下载授权？原组件和缓存暂时保留，配置、素材与任务历史不会删除。'],
          ['回退保留版本','rollback','先校验并实际检测保留版本，通过后切换回去。用户数据不变，当前新版目录也保留。确认回退？']]) {
          if(action==='uninstall'&&!row.current_version)continue;
          if(action==='rollback'&&!row.can_rollback)continue;
          const button=node('button',label);button.type='button';button.disabled=busy||readOnly;
          button.addEventListener('click',async()=>{
            if(!window.confirm(row.title+'：'+explanation))return;
            button.disabled=true;
            try{render(await api('/api/components/'+action,{id:row.id,confirmed:true}));}
            catch(error){message.textContent=error.message;}
            finally{button.disabled=false;}
          });section.append(button);
        }
        if(row.install_path){const detail=node('details');detail.append(node('summary','安装位置'),node('p',row.install_path));section.append(detail);}
        if(row.error)section.append(node('p',row.error));
        list.append(section);
      }
      message.textContent=value.update_policy;
      if(value.plugin_updates?.state==='BLOCKED')message.textContent+=' '+value.plugin_updates.reason;
      if(value.plugin_updates?.catalog_warning)message.textContent+=' '+value.plugin_updates.catalog_warning;
    }
    async function poll(verify=false) {
      try{render(await api('/api/components/status'+(verify?'?verify=1':'')));}
      catch(error){message.textContent='组件状态暂不可用：'+error.message;}
    }
    async function tick() {
      if(!document.hidden&&(dialog.open||busy))await poll();
      setTimeout(tick,busy?2000:15000);
    }
    function show(){
      if(!document.getElementById('page-settings')?.classList.contains('active'))document.querySelector('[data-page="settings"]')?.click();
      dialog.showModal();poll(true);
    }
    open.addEventListener('click',show);
    window.SeedanceComponents={open:show};
    close.addEventListener('click',()=>dialog.close());
    card.append(open,dialog);poll().finally(tick);
  }
  function updates(card) {
    const section=node('section');section.style.marginTop='22px';section.append(node('h4','软件更新'));
    const message=node('p','正在读取更新状态…');message.setAttribute('role','status');
    const channel=node('select');channel.setAttribute('aria-label','更新通道');
    for(const [value,text] of [['stable','稳定通道'],['beta','测试通道']]) {
      const option=node('option',text);option.value=value;channel.append(option);
    }
    const check=node('button','检查更新'),download=node('button','下载已验证版本'),install=node('button','安装并重启'),cancel=node('button','取消尚未开始的更新');
    for(const button of [check,download,install,cancel]){button.type='button';button.style.marginLeft='8px';button.disabled=true;}
    cancel.hidden=true;
    const release=node('p'),history=node('details');history.append(node('summary','更新历史'));
    const entries=node('ul');history.append(entries);
    let target=null,busy=false,state=null;
    function render(value) {
      state=value;
      cancel.hidden=value.maintenance?.state!=='prepared';cancel.disabled=false;
      const job=value.job||{};busy=job.state==='running';
      check.disabled=busy||value.state!=='ready';channel.disabled=busy;
      if(job.action==='check'&&job.state==='completed'&&job.result?.target_id) {
        target={...job.result,downloaded:false};
        release.textContent='可用版本：'+target.version+' / '+target.build+' · '+(target.length/1048576).toFixed(1)+' MB。'+target.notes;
      }
      if(job.action==='download'&&job.state==='completed'&&target?.target_id===job.result?.target_id)target.downloaded=true;
      download.disabled=busy||value.state!=='ready'||!target;
      install.disabled=busy||value.state!=='ready'||!target?.downloaded;
      if(value.maintenance)message.textContent='更新维护状态：'+value.maintenance.state+'。新写入已暂停；安装未开始时可取消，已接管的更新需查看恢复结果。';
      else if(value.state==='BLOCKED')message.textContent=value.reason;
      else if(job.state==='failed')message.textContent='操作未完成：'+job.error+'。现有版本和数据保持受保护。';
      else if(job.state==='running')message.textContent=job.action==='download'
        ?'正在下载并核验：'+((job.downloaded_bytes||0)/1048576).toFixed(1)+' / '+((job.total_bytes||0)/1048576).toFixed(1)+' MB'
        :job.action==='install'?'正在核验发布者、备份数据并准备独立更新程序，请勿关闭电脑。':'正在验证更新元数据…';
      else if(job.action==='install'&&job.result?.state==='helper_started')message.textContent='独立更新程序已启动；安装结果以重启后的更新历史为准。';
      else if(job.result?.state==='up_to_date')message.textContent='已通过签名元数据核对，当前是此通道的最新版本。';
      else message.textContent='当前版本 '+value.current_version+' / '+value.current_build+'。自动检查不会自动安装。';
      entries.replaceChildren();
      for(const event of (value.history||[]).slice(0,10))entries.append(node('li',String(event.state)+' · '+String(event.message||'')));
    }
    async function action(path,body) {
      if(busy)return;busy=true;check.disabled=download.disabled=install.disabled=true;
      try{render(await api(path,body));}
      catch(error){message.textContent=error.message;busy=false;check.disabled=state?.state!=='ready';}
    }
    check.addEventListener('click',()=>action('/api/updates/check',{channel:channel.value}));
    channel.addEventListener('change',()=>{target=null;release.textContent='';download.disabled=install.disabled=true;});
    download.addEventListener('click',()=>target&&action('/api/updates/download',{target_id:target.target_id}));
    install.addEventListener('click',()=>{
      if(target?.downloaded&&window.confirm('确认安装 '+target.version+'？系统将暂停新任务、备份数据并关闭当前窗口。独立更新程序需要额外临时磁盘空间。请先保存工作；有未完成或结果未知的任务时会拒绝更新。'))
        action('/api/updates/install',{target_id:target.target_id,confirmed:true});
    });
    cancel.addEventListener('click',async()=>{
      if(state?.maintenance?.state==='prepared'&&window.confirm('确认取消尚未被独立程序接管的更新，并恢复任务写入？')){
        try{await api('/api/updates/cancel',{transaction_id:state.maintenance.transaction_id});render(await api('/api/updates/status'));}
        catch(error){message.textContent=error.message;}
      }
    });
    section.append(message,channel,check,download,install,cancel,release,history);card.append(section);
    async function poll() {
      try {
        if(!state||(!document.hidden&&document.getElementById('page-settings')?.classList.contains('active')))
          render(await api('/api/updates/status'));
      }catch(error){message.textContent='更新状态暂不可用：'+error.message;}
      setTimeout(poll,busy?1500:30000);
    }
    poll();
  }
  function mount() {
    const page=document.getElementById('page-settings');
    if(!page||document.getElementById('desktop-maintenance'))return;
    const card=node('section');card.id='desktop-maintenance';card.className='card';
    card.style.cssText='padding:22px;margin:20px 0';
    card.append(node('h3','应用维护'));
    card.append(node('p','诊断包只包含版本、依赖和任务状态汇总，不含密钥、对话、素材、数据库或原始日志。'));
    const status=node('p');status.setAttribute('role','status');
    const download=node('button','导出安全诊断包');download.type='button';
    download.addEventListener('click',async()=>{
      download.disabled=true;
      try {
        const response=await fetch('/api/desktop/diagnostics',{cache:'no-store'});
        if(!response.ok||!response.headers.get('Content-Type')?.includes('application/zip'))
          throw new Error('诊断包导出失败，请检查本机服务状态');
        const url=URL.createObjectURL(await response.blob());
        const link=node('a');link.href=url;link.download='SeedanceStudio_Diagnostics.zip';link.click();
        setTimeout(()=>URL.revokeObjectURL(url),5000);
        status.textContent='已生成诊断包，可交给维护人员。';
      }catch(error){status.textContent=error.message;}
      finally{download.disabled=false;}
    });
    card.append(download,status);components(card);updates(card);page.append(card);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
})();
