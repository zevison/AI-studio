const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
test('saved image preview does not call a removed alias initializer',()=>{
 const nodes=new Map();
 const context={state:{},$:id=>{if(!nodes.has(id))nodes.set(id,{disabled:true,innerHTML:''});return nodes.get(id)}};
 vm.createContext(context);
 const start=source.indexOf('function setCurrentImage(');
 vm.runInContext(source.slice(start,source.indexOf('\n',start)),context);
 context.setCurrentImage({id:'fixture',url:'/public/assets/fixture.png'});
 assert.match(nodes.get('#imageResult').innerHTML,/fixture.png/);
 assert.equal(nodes.get('#addImageToVideo').disabled,false);
});
const body=source.slice(source.indexOf('async function restoreWorkspaceDraft()'),source.indexOf('const DRAFT_IDS='));
for(const touched of [false,true])test('startup restore respects navigationTouched='+touched,async()=>{
  let activated=[],scrolled=0;const nodes=new Map();
  const context={state:{navigationTouched:touched},api:async p=>p.endsWith('/drafts')?{global:{data:{page:'hypit',scrollY:300},updated_at:1}}:[],
    $:key=>{if(!nodes.has(key))nodes.set(key,{value:'',classList:{toggle(){}}});return nodes.get(key)},
    document:{querySelector:()=>null},normalizePromptMentions:v=>v,
    activatePage:p=>activated.push(p),window:{scrollTo(){scrolled++}},setTimeout:fn=>fn(),console};
  for(const name of ['updateModeUi','updateResolutionControls','renderAssets','setImageMode','renderImageSourceList','renderDeepSource','setWorkspaceTab'])context[name]=()=>{};
  vm.createContext(context);vm.runInContext(body+';this.restore=restoreWorkspaceDraft',context);
  await context.restore();assert.equal(context.state.draftRestoring,false);
  assert.equal(activated.length,touched?0:1);assert.equal(scrolled,touched?0:1);
});
test('finished task duration stops increasing after completion',()=>{
  const context={};vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function taskElapsedSeconds('),source.indexOf('function updateTaskLiveFields(')),context);
  const task={created_at:100,updated_at:110,status:'completed'};
  assert.equal(context.taskElapsedSeconds(task,99999),10);
  assert.equal(context.taskElapsedSeconds({...task,status:'running'},150),50);
});

test('completed local task shows its real local ID instead of waiting for a provider ID',()=>{
 const context={esc:v=>String(v),fmtElapsed:v=>String(v),taskElapsedSeconds:()=>1,taskCenterTypeLabel:()=> 'Hypit',taskCenterStatusClass:()=>'',taskWorkspace:()=> 'hypit',statusLabel:v=>v,taskCenterTitle:()=> 'Local export'};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function globalTaskCard('),source.indexOf('\n',source.indexOf('function globalTaskCard('))),context);
 const html=context.globalTaskCard({id:'local-123',status:'completed'},true);
 assert.match(html,/local-123（本地编号）/);assert.doesNotMatch(html,/等待平台任务号/);
 const remote=context.globalTaskCard({id:'local-123',status:'completed',provider_task_id:'provider-456'},true);
 assert.match(remote,/Task ID：provider-456/);
});
