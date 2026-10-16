"""Mocked provider regression: zero external paid requests."""
from __future__ import annotations
import io, json, sys, tempfile, base64
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from core import ai_assistant,db,config,gpt_pro_v41 as gpt,openlux

class FakeResponse:
    def __init__(self, chunks, content_type='text/event-stream'):
        self.parts=chunks
        self.headers={'Content-Type':content_type}
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def __iter__(self):return iter(self.parts)
    def read(self,n=-1):return b''.join(self.parts)

def run():
 with tempfile.TemporaryDirectory() as folder:
  path=Path(folder)/'scratch.db'
  with patch.object(config,'DB_PATH',path),patch.object(db,'DB_PATH',path),patch.object(gpt.secrets_store,'get',return_value='fake_test_key'):
   db.init_db();ai_assistant.schema()
   # Model list is read-only GET and cached.
   def model_data(req,timeout=0):
    assert req.method=='GET' and req.full_url.endswith('/v1/models')
    return FakeResponse([json.dumps({'data':[{'id':'gpt-6-astra'},{'id':'gpt-5.6-sol'},{'id':'gpt-image-2'}]}).encode()],content_type='application/json')
   with patch('urllib.request.urlopen',side_effect=model_data) as mock:
    listing=gpt.models(force=True)
    assert listing['verified'] and 'gpt-6-astra' in listing['models'] and 'gpt-image-2' not in listing['models']
    assert mock.call_count==1
   conv=ai_assistant.new_conversation(); events=[]
   def stream(req,timeout=0):
    assert req.method=='POST' and req.full_url.endswith('/v1/chat/completions')
    body=json.loads(req.data);assert body['model']=='gpt-6-astra' and body['stream'] is True
    assert 'tools' not in body and len(body['messages'])==2
    return FakeResponse([b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0\xe5\xa5\xbd"}}]}\n\n',b'data: {"choices":[{"delta":{"content":"\xef\xbc\x81"}}]}\n\n',b'data: [DONE]\n\n'])
   with patch('urllib.request.urlopen',side_effect=stream) as mock:
    gpt.chat_events({'conversation_id':conv['id'],'message':'你好','model':'gpt-6-astra','approve_model_cost':True,'thinking':'default','mode':'chat'},lambda name,data:events.append((name,data)))
    assert mock.call_count==1
   assert [n for n,d in events]==['begin','delta','delta','done']
   assert events[-1][1]['complete'] and ''.join(x['text'] for n,x in events if n=='delta')=='你好！'
   assert len(ai_assistant.messages(conv['id']))==2
   # Strong model normal chat does not inherit product/video context or tools.
   with patch('urllib.request.urlopen',side_effect=AssertionError('Unexpected repeat paid request')):
    # Sending within same conversation would be a new explicit user action; do not do that here.
    pass
   conv2=ai_assistant.new_conversation();ev=[]
   with patch.object(openlux,'request_json',return_value={'model':'gpt-6-astra','output':[{'content':[{'text':'现在的信息来自供应商联网工具。'}]}],'usage':{'total_tokens':39}}) as provider:
    gpt.chat_events({'conversation_id':conv2['id'],'message':'查一条新闻','model':'gpt-6-astra','approve_model_cost':True,'mode':'web'},lambda a,b:ev.append((a,b)))
    assert provider.call_count==1 and provider.call_args.args[1]=='/v1/responses'
    assert provider.call_args.args[2]['tools']==[{'type':'web_search'}]
   assert ev[-1][0]=='done'
   assert '现在的信息来自供应商联网工具' in ai_assistant.messages(conv2['id'])[-1]['content']
   # Direct vision payload must originate from user attachment; no product library fetch.
   img=base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'0'*10).decode()
   parsed=gpt._files([{'name':'demo.png','data':img}]);assert parsed[0]['kind']=='image' and parsed[0]['value'].startswith('data:image/png;base64,')
   assert gpt._files([{'name':'note.txt','data':base64.b64encode('中文资料'.encode()).decode()}])[0]['value']=='中文资料'
   try:gpt._files([{'name':'demo.exe','data':img}]);assert False
   except ValueError:pass
   # Unknown upstream response never triggers automatic paid POST retry.
   conv3=ai_assistant.new_conversation();ev=[]
   with patch('urllib.request.urlopen',side_effect=TimeoutError('Read timed out')) as mock:
    gpt.chat_events({'conversation_id':conv3['id'],'message':'hello','model':'gpt-6-astra','approve_model_cost':True},lambda a,b:ev.append((a,b)))
    assert mock.call_count==1
   assert ev[-1][0]=='error'
   assert ai_assistant.messages(conv3['id'])[-1]['role']=='user'
   print('PASS: model list; streamed GPT-6; isolated history; provider search; vision/text; network timeout no retry')

if __name__=='__main__':run()
