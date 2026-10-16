"""Offline router test matching the exact 15s photo + dance video scenario."""
import sys,json,tempfile,threading,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import intelligent_router as router,db,config,openlux,smart_analysis,media_preflight
from app import Handler

DANCE=('以@video 1作为唯一舞蹈动作和节奏参考，用@image 1中的女孩在照片原有的'
       '红色建筑前完成15秒舞蹈，人物一致且保留原照片中的背景，不出现变形。')

def test_router_rules():
 ctx=dict(task_type='bulk',mode='reference',prompt=DANCE,duration=15,images=1,
          videos=1,audios=0,ratio='9:16',resolution='720p',native_audio=True)
 try:router.choose(ctx,mode='cheap');raise AssertionError('strict prompt must not be silently relaxed')
 except ValueError as e:assert '勾选' in str(e)
 plan=router.choose(dict(ctx,approx_confirmed=True),mode='cheap')
 assert plan['task_type']=='bulk' and plan['requested_task_type']=='bulk',plan
 assert plan['selected_model']==router.SEED,plan
 assert plan['motion_fidelity']=='approx' and plan['detected_reference_action'],plan
 assert plan['quote']['estimated_total_usd'] is None,plan
 assert any('Seedance 并不代表最低费用' in w for w in plan['warnings']),plan
 # Incompatible strict capability is still a hard block.
 for field in ({'motion_fidelity':'strict','approx_confirmed':True},{'task_type':'motion','approx_confirmed':True},{'motion_fidelity':'background','approx_confirmed':True}):
  try:router.choose(dict(ctx,**field),mode='cheap');raise AssertionError(field)
  except ValueError as e:assert '未付费' in str(e)
 # Cheap simple video can use Vidu; old defaults preserved.
 simple=dict(task_type='bulk',mode='text',prompt='US product hero ad',duration=5,images=0,videos=0,audios=0,
             ratio='9:16',resolution='720p',native_audio=False)
 assert router.choose(simple,mode='cheap')['selected_model']==router.VIDU
 assert router.choose(simple,mode='auto')['selected_model']==router.SEED
 # auto with reference video is not silently strict motion transfer
 assert router.choose(dict(ctx,task_type='auto',approx_confirmed=True),mode='auto')['task_type']=='replica'
 assert len(router.COMMON_MODELS)==3 and set(router.COMMON_MODELS)==set(router.ACTIVE)
 assert len(router.catalog())>=10  # keep research catalog intact
 print('PASS: strict prompt needs confirmation, near motion uses compatible Seedance, cheap no false savings, strict background/transfer blocked')

def test_http_preview():
 with tempfile.TemporaryDirectory(prefix='route39815_') as td,patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(media_preflight,'preview_specs',return_value={'ok':True,'warnings':[]}),patch.object(smart_analysis,'analyze_references',return_value={'ok':True,'used':False,'summary':''}):
  db.init_db();folder=Path(td)
  for kind,name,idx in [('video','dance.mp4','vid'),('image','girl.jpg','img')]:
   loc=folder/name;loc.write_bytes(b'fake')
   db.execute('INSERT INTO assets(id,original_name,stored_name,kind,mime,path,sha256,size_bytes,duration,width,height,fps,has_audio,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (idx,name,name,kind,'video/mp4' if kind=='video' else 'image/jpeg',str(loc),'a'*64 if idx=='vid' else 'b'*64,5,15 if kind=='video' else None,1080,1920,30,kind=='video',db.now()))
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  url='http://127.0.0.1:'+str(server.server_address[1])
  def http(d):
   request=urllib.request.Request(url+'/api/video/direct-preview',json.dumps(d).encode(),{'Content-Type':'application/json'},method='POST')
   try:
    with urllib.request.urlopen(request,timeout=8) as h:return h.status,json.load(h)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  payload={'prompt':DANCE,'asset_ids':['vid','img'],'video_mode':'auto','duration':15,'ratio':'9:16','resolution':'720p',
           'generate_audio':True,'video_model':'auto','task_type':'bulk','routing_mode':'cheap','motion_fidelity':'approx'}
  try:
   status,res=http(payload)
   assert status==409 and '勾选' in res['error'],res
   assert not db.all_('SELECT * FROM generation_previews'), 'Do not create preview for unapproved relaxation'
   status,res=http(dict(payload,approx_confirmed=True))
   assert status==200,res
   pv=res['data'];decision=pv['router_decision']
   assert decision['task_type']=='bulk' and decision['requested_task_type']=='bulk',decision
   assert pv['model'].startswith('doubao-seedance-2-5'),pv
   assert pv['media_count']==2,pv
   token=pv['preview_token'];record=db.one('SELECT payload_json FROM generation_previews WHERE token=?',(token,))
   saved=json.loads(record['payload_json'])
   assert '用户已明确同意低成本近似动作参考' in saved['prompt']
   assert saved['router_decision']['approx_confirmed'] is True
   assert '已明确同意' not in payload['prompt']
   assert not db.all_("SELECT * FROM tasks WHERE kind='video'")
   print('PASS: HTTP actual bulk/cheap/reference preview, consent required, persisted model+purpose, no paid task')
  finally:server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':test_router_rules();test_http_preview()
