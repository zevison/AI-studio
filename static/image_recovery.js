/* Local image-response recovery. This module has no model generation endpoint. */
(()=>{
 'use strict';
 const result=document.getElementById('imageResult');if(!result)return;
 const box=document.createElement('div');box.className='image-save-recovery';box.hidden=true;result.before(box);
 async function load(){
  try{
   const response=await fetch('/api/image/recovery',{cache:'no-store'}),body=await response.json();if(!response.ok||!body.ok)return;
   box.replaceChildren();box.hidden=!(body.data||[]).length;
   for(const task of body.data||[]){
    const row=document.createElement('div'),text=document.createElement('span'),button=document.createElement('button');
    text.textContent='图片接口已返回，本地保存待恢复（'+task.id.slice(-8)+'）。';
    button.textContent='仅重新保存原图片（不生成）';
    button.onclick=async()=>{
     button.disabled=true;
     try{
      const response=await fetch('/api/tasks/retry-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:task.id})}),body=await response.json();
      if(!response.ok||!body.ok)throw Error(body.error||'保存失败');
      row.remove();box.hidden=!box.children.length;
      try{
       if(body.data?.version?.asset&&typeof setCurrentImage==='function')setCurrentImage(body.data.version.asset);
       if(typeof refreshImageVersions==='function')await refreshImageVersions();
      }catch{result.textContent='图片已保存；界面刷新失败，请重新打开历史版本。';}
      await load();
     }catch(error){text.textContent='原响应仍保留。保存失败：'+error.message;button.disabled=false;}
    };
    row.append(text,button);box.append(row);
   }
  }catch{}
 }
 window.showPendingImageSave=()=>load();
 document.querySelector('button[data-page="image"]')?.addEventListener('click',load);
 load();
})();
