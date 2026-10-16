"""No-charge provider adapter contract tests."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import video_adapters as va

def fake_spec(role='first_frame',kind='image'):
 return {'role':role,'asset':{'kind':kind,'remote_url':'https://example.com/test.png' if kind=='image' else 'https://example.com/clip.mp4'}}

def test_contracts():
 p,q,b=va.build(va.VIDU,'US TikTok ad',[], '9:16','720p',5,True,None)
 assert p=='/ent/v2/text2video' and b['model']=='viduq3-turbo' and b['aspect_ratio']=='9:16'
 assert q.endswith('/creations')
 p,q,b=va.build(va.VIDU,'move',[fake_spec()],'adaptive','720p',5,False,None)
 assert p=='/ent/v2/img2video' and b['images']==['https://example.com/test.png']
 p,q,b=va.build(va.VIDU,'transition',[fake_spec(),fake_spec('last_frame')],'adaptive','720p',5,True,None)
 assert p=='/ent/v2/start-end2video' and len(b['images'])==2
 try:va.build(va.VIDU,'wrong',[fake_spec('reference_video','video')],'9:16','720p',5,True,None)
 except va.RouteError:pass
 else:raise AssertionError('Vidu illegal video reference passed')
 p,q,b=va.build(va.H3,'Cinematic US ad',[fake_spec('reference_video','video')],'adaptive','2K',15,True,None)
 assert p=='/minimax/v2/video_generation' and b['model']=='MiniMax-H3'
 assert b['content'][1]['video_url']['url']=='https://example.com/clip.mp4' and 'ratio' not in b
 assert va.status({'task':{'status':'succeeded','content':{'url':'https://example.com/complete.mp4'}}})=='completed'
 assert va.result_url({'task':{'status':'succeeded','content':{'url':'https://example.com/complete.mp4'}}})=='https://example.com/complete.mp4'
 assert va.result_url({'state':'success','creations':[{'cover_url':'https://example.com/cover.jpg','url':'https://example.com/clip.mp4'}]})=='https://example.com/clip.mp4'
 assert va.model_diagnostic(va.VIDU,list_fn=lambda:['viduq3-turbo'])['available']
 assert not va.model_diagnostic(va.H3,list_fn=lambda:['doubao-seedance-2-5-260628'])['available']
 print('provider contract tests passed')
if __name__=='__main__':test_contracts()
