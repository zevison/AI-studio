"""HTTP integration smoke. Real loopback server; provider requests are mocked (zero cost)."""
import json,sys,tempfile,threading
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from core import config,db,ai_assistant
from app import Handler
import requests
from core import gpt_pro_v41

class DummyResp:
 headers={'Content-Type':'text/event-stream'}
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def __iter__(self):
  for x in [b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n',b'data: [DONE]\n\n']:
   yield x

with tempfile.TemporaryDirectory() as temp:
 p=Path(temp)/'studio.db'
 with patch.object(config,'DB_PATH',p),patch.object(db,'DB_PATH',p),patch.object(gpt_pro_v41.secrets_store,'get',return_value='fake'),patch('urllib.request.urlopen',return_value=DummyResp()) as model_call:
  db.init_db(); ai_assistant.schema()
  srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
  try:
   host='http://127.0.0.1:'+str(srv.server_address[1])
   conv=requests.post(host+'/api/assistant/new',json={},timeout=4).json()['data']
   response=requests.post(host+'/api/gpt-v41/chat/stream',json={'conversation_id':conv['id'],'message':'Hi','model':'gpt-6-astra','approve_model_cost':True},timeout=8)
   text=response.text
   assert response.status_code==200
   assert 'event: begin' in text and 'event: delta' in text and 'event: done' in text and 'Hello ' in text
   assert model_call.call_count==1
   msgs=requests.get(host+'/api/assistant/messages?conversation_id='+conv['id'],timeout=4).json()['data']
   assert len(msgs)==2 and msgs[-1]['content']=='Hello world'
   html=requests.get(host+'/',timeout=4).text
   assert 'aiSyncModels' in html and 'aiWebSearch' in html and 'GPT-6 Astra' in html
   memory=requests.get(host+'/api/gpt-v4/memory',timeout=4).json()['data']
   assert 'items' in memory
   print('PASS HTTP: one paid mock request, SSE output, model selector, unchanged memory and chat history')
  finally:
   srv.shutdown();srv.server_close();thread.join(timeout=2)
