"""Installed 00_START -> HTTP -> genuine HTML Runtime/Studio; no models."""
import argparse,hashlib,json,os,shutil,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--root',required=True)
parser.add_argument('--port',type=int,default=51610);parser.add_argument('--report',default='installed-free-smoke.json');args=parser.parse_args()
install=Path(args.root)
# Use the actual fresh guest profile, not a developer-machine profile.
sys.path.insert(0,str(install))
from core import db,config,media
base='http://127.0.0.1:'+str(args.port)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(route,value=None,token=None,origin=None):
    headers={'Content-Type':'application/json'}
    if token:headers['X-Hypit-Session']=token
    if origin:headers['Origin']=origin
    req=urllib.request.Request(base+route,data=None if value is None else json.dumps(value).encode(),headers=headers)
    with opener.open(req,timeout=180) as response:
        data=json.load(response)
    assert data.get('ok'),data
    return data['data']
health=request('/api/bootstrap-health');assert Path(health['root_dir']).resolve()==install.resolve()
host=request('/api/hypit/health');assert host['available'] and host['local_build'] and host['skill_installed']
system=request('/api/system/health');assert system['required_ok'],system
optional_missing=system.get('missing_recommended',[])
token=request('/api/hypit/session')['token']
for bad in ('csrf','origin'):
    try:
        request('/api/hypit/create',{},token if bad=='origin' else 'forged',
            'https://attacker.example' if bad=='origin' else None)
        raise AssertionError('Forged write accepted')
    except urllib.error.HTTPError as exc:assert exc.code==403
prior=json.loads((ROOT/'outputs/product-live-agent/result.json').read_text()) if (ROOT/'outputs/product-live-agent/result.json').is_file() else None
projects=list((Path(__file__).resolve().parent/'fixtures/projects').glob('hp_*'))
original=next(p for p in projects if 'end="1s"' in (p/'main.svml').read_text())
aid='install-fixture-'+uuid.uuid4().hex
asset=config.ASSET_DIR/(aid+'.mp4');shutil.copy2(original/'assets/material-1.mp4',asset)
probe=media.probe(asset);assert probe['ok']
db.execute('INSERT INTO assets(id,path,kind,sha256,duration,width,height) VALUES(?,?,?,?,?,?,?)',
    (aid,str(asset),'video',hashlib.sha256(asset.read_bytes()).hexdigest(),probe['duration'],probe['width'],probe['height']))
row=request('/api/hypit/create',{'objective':'Installed offline acceptance: render the previously Agent-authored one-second Film.',
    'title':'安装验收：免费 Film 构建','asset_ids':[aid]},token)
folder=config.DATA_DIR/'hypit_projects'/row['id']
shutil.copy2(original/'main.svml',folder/'main.svml');shutil.copy2(original/'look.svs',folder/'look.svs')
validation=request('/api/hypit/validate',{'id':row['id']},token);assert validation['ok'],validation
request('/api/hypit/build',{'id':row['id']},token)
deadline=time.monotonic()+180
while time.monotonic()<deadline:
    final=request('/api/hypit/project?id='+row['id'])
    if final['status'] not in {'building','exporting','validating'}:break
    time.sleep(.5)
assert final['status']=='completed',final
result=final['metadata']['result'];assert .95<=result['media']['duration']<=1.1,result
studio=request('/api/hypit/studio',{'id':row['id']},token)
with opener.open(studio['url'],timeout=20) as response:assert response.status==200
after_studio=request('/api/hypit/project?id='+row['id'])
assert after_studio['status']=='completed',after_studio['status']
output={'install_root':str(install),'launcher':'00_START.cmd','http':True,'local_runtime':True,
    'completed_status_preserved_after_studio':True,
    'official_studio_http':True,'optional_missing':optional_missing,'clean_windows_acceptance':False,'project_id':row['id'],'result':result,'sdk':host,'system_required_ok':True,
    'red_team':['forged CSRF rejected','cross-origin write rejected'],'paid_calls':0}
(ROOT/'outputs'/args.report).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'project_id':row['id'],'status':final['status'],'duration':result['media']['duration'],
    'official_studio':studio['url'],'paid_calls':0},ensure_ascii=False))
