/* Read-only wallet: one in-flight lookup; visible-page polling only. */
(()=>{'use strict';
 const byId=id=>document.getElementById(id),open=byId('accountBalanceOpen'),dialog=byId('accountBalanceDialog');
 if(!dialog)return;
 const refresh=byId('accountBalanceRefresh'),value=byId('accountBalanceValue'),status=byId('accountBalanceStatus');
 const top=byId('accountWalletValue'),stamp=byId('accountWalletStamp'),topRefresh=byId('accountWalletRefresh');
 const remote=typeof window!=='undefined'&&window.STUDIO_CONTEXT?.remote===true;
 const settings=byId('accountBalanceSettings');
 if(remote)settings.textContent='账户配置说明';
 let pending=false,lastAttempt=0,lastGood=null;
 function display(amount,message,stale=false){
  const valid=typeof amount==='number'&&Number.isFinite(amount)&&amount>=0;
  const text=valid?amount.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:4}):'暂不可用';
  value.textContent=text;status.textContent=message;
  const accountStatus=byId('openLuxAccountStatus');if(accountStatus)accountStatus.textContent=(valid?'账户余额 '+text+'；':'')+message;
  const costStatus=byId('costSettingsStatus');if(costStatus)costStatus.textContent='余额与右上角同步。每次生成须单独核验当前账号报价；报价不完整时不显示推算费用，最终扣费以 OpenLux 账单为准。';
  if(top){top.textContent=text+(valid&&stale?'（旧值）':'');top.dataset.stale=String(stale);top.title=message;}
 }
 async function load(force=false){
  if(pending)return;pending=true;lastAttempt=Date.now();refresh.disabled=true;if(topRefresh)topRefresh.disabled=true;
  status.textContent='正在只读查询 OpenLux 账户钱包';if(stamp)stamp.textContent='正在更新…';
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  try{
   const response=await fetch('/api/openlux/balance'+(force?'?refresh=1':''),{signal:controller.signal,cache:'no-store'});
   const result=await response.json();if(!response.ok||!result.ok)throw Error('余额接口暂不可用');
   const balance=result.data||{},amount=balance.available;
   if(balance.ok&&typeof amount==='number'&&Number.isFinite(amount)&&amount>=0){
    if(!balance.stale)lastGood=amount;
    display(amount,balance.stale?'上次成功余额；本次刷新失败，请勿作为当前可用余额。':'已读取 OpenLux 账户钱包；按设置中的额度折算基数换算。',Boolean(balance.stale));
    if(stamp)stamp.textContent=balance.stale?'刷新失败 · 上次余额':'更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
   }else{display(lastGood,remote?'未能读取账户余额。可手动刷新；若持续失败，请联系主机管理员检查网络与账户绑定。':'未能读取账户余额。请检查网络和接口密钥；若密钥无账户权限，请在设置中绑定账号访问令牌。',lastGood!==null);if(stamp)stamp.textContent='未连接 · 点击余额查看说明';}
  }catch(error){
   display(lastGood,error.name==='AbortError'?'查询超时，请检查网络后手动刷新。':'无法读取余额，请检查服务与网络后手动刷新。',lastGood!==null);
   if(lastGood===null)value.textContent='查询失败';if(stamp)stamp.textContent='刷新失败 · 可手动重试';
  }finally{clearTimeout(timer);pending=false;refresh.disabled=false;if(topRefresh)topRefresh.disabled=false;}
 }
 function show(){if(!dialog.open)dialog.showModal();load();}
 open?.addEventListener('click',show);if(top)top.addEventListener('click',show);
 refresh.addEventListener('click',()=>load(true));if(topRefresh)topRefresh.addEventListener('click',()=>load(true));
 byId('accountBalanceClose').addEventListener('click',()=>dialog.close());
 settings.addEventListener('click',()=>{if(remote){status.textContent='远程网页不能修改主机账户凭据。请联系主机管理员在本机「状态与设置」中配置；完成后点击刷新余额。';return;}dialog.close();document.querySelector('.nav[data-page="settings"]')?.click();byId('setOpenLuxAccountToken')?.scrollIntoView({block:'center',behavior:'smooth'});});
 if(top){load();setInterval(()=>{if(document.visibilityState==='visible')load();},30000);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&Date.now()-lastAttempt>=30000)load();});}
})();
