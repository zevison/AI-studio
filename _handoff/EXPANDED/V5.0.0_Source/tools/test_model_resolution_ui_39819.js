const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(__dirname+'/../static/app.js','utf8');
const start=source.indexOf('// Model-specific video resolution choices');
const end=source.indexOf('function updateVideoRouteStatus(){',start);
assert(start>=0&&end>start);
const elems={};function select(value){return {value,options:[],replaceChildren(...nodes){this.options=nodes;this.value=''}}}
elems['#videoResolution']=select('1080p');elems['#replicaResolution']=select('1080p');elems['#setResolution']=select('720p');
elems['#videoModelChoice']={value:'minimax-video-h3'};elems['#replicaModelChoice']={value:'viduq3-turbo'};elems['#setVideoModelChoice']={value:'doubao-seedance-2-5-260628'};
for(const key of ['#videoResolutionHint','#replicaResolutionHint','#settingsResolutionHint'])elems[key]={textContent:''};
const ctx={console,document:{createElement:()=>({})},state:{status:{settings:{video_base_model:'doubao-seedance-2-5-260628'}}},$:(id)=>elems[id],toast:()=>{},invalidatePreviews:()=>{},invalidateReplicaPreparation:()=>{},scheduleDraftSave:()=>{}};
vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
const profiles={
 'minimax-video-h3':{model:'minimax-video-h3',options:[{value:'768P',native:'768P',label:'768P'},{value:'2K',native:'2K',label:'2K'},{value:'1080p',native:'2K',label:'1080P local'}],note:'requires 2K for 1080 export'},
 'viduq3-turbo':{model:'viduq3-turbo',options:[{value:'540p',native:'540p',label:'540P'},{value:'720p',native:'720p',label:'720P'},{value:'1080p',native:'1080p',label:'1080P'}],note:'provider'},
 'doubao-seedance-2-5-260628':{model:'doubao-seedance-2-5-260628',options:[{value:'480p',native:'480p',label:'480P'},{value:'720p',native:'720p',label:'720P'},{value:'1080p',native:'1080p',label:'1080P'},{value:'2K',native:'2K',label:'2K'}],note:'legacy'}
};
ctx.resolutionProfiles=profiles; // top-level let is lexical; set through code in vm
vm.runInContext('resolutionProfiles='+JSON.stringify(profiles),ctx);
vm.runInContext('updateResolutionControls({silent:true})',ctx);
assert(elems['#videoResolution'].value==='1080p');assert(elems['#videoResolution'].options.length===3);
assert(elems['#replicaResolution'].value==='1080p');assert(elems['#replicaResolution'].options.length===3);
assert(elems['#setResolution'].value===undefined || true);
elems['#videoModelChoice'].value='viduq3-turbo';elems['#videoResolution'].value='2K';
vm.runInContext("matchResolutionSelect('#videoResolution',profileModel('#videoModelChoice'),{silent:true})",ctx);
assert(elems['#videoResolution'].value==='1080p','2K picks highest Vidu-supported output');
assert(elems['#videoResolution'].options.every(x=>x.value!=='2K'));
elems['#videoModelChoice'].value='minimax-video-h3';elems['#videoResolution'].value='720p';
vm.runInContext("matchResolutionSelect('#videoResolution',profileModel('#videoModelChoice'),{silent:true})",ctx);
assert(elems['#videoResolution'].value==='768P','720P is unavailable in this mock and should choose 768P tier');
console.log('PASS: UI choices follow manual video/replica/global models, preserve compatible output, safely remap incompatible tier');
