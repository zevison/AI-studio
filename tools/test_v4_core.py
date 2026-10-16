"""No-paid-call smoke tests. Run on a scratch database only."""
import os, tempfile,sys
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from core import config,db,ai_assistant as ai,gpt_memory_v4 as mem,bright_tiktok as bd,openlux,secrets_store,hot_radar

with tempfile.TemporaryDirectory() as temp:
 path=Path(temp)/'studio.db'
 with patch.object(config,'DB_PATH',path),patch.object(db,'DB_PATH',path):
  db.init_db();ai.schema();bd.schema()
  chat=ai.new_conversation()
  item=mem.save({'content':'所有英文附中文解释'})
  assert '所有英文' in mem.context_text()
  with patch.object(secrets_store,'get',return_value='fake_key'),patch.object(openlux,'request_json',return_value={'model':'test-model','choices':[{'message':{'content':'普通问答回复'}}],'usage':{'total_tokens':12}}) as mocked:
   response=ai.send({'conversation_id':chat['id'],'message':'你好','model':'test-model','approve_model_cost':True,'tools_enabled':True,'product_ids':['hidden_product']})
  sent=mocked.call_args.args[2]
  assert 'tools' not in sent and 'product_ids' not in str(sent) and sent['messages'][0]['role']=='system'
  assert response['answer']=='普通问答回复' and len(ai.messages(chat['id']))==2
  record=mem.delete_conversation({'conversation_id':chat['id'],'confirmed':True})
  assert record['deleted'] and not ai.conversations() and mem.list_memories()['items']
  assert mem.delete({'id':item['id']})['deleted']
  # No Bright Data secret, quota verification -> reject before any provider request.
  with patch.object(bd,'_request',side_effect=AssertionError('Should never hit paid API')):
   try:bd.submit({'product_id':'none','quantity':20,'confirmed':True})
   except ValueError:pass
   else:raise AssertionError('Missing product must fail')
  assert bd.cfg()['confirmed_at']==0
  print('V4 smoke PASS: GPT isolated, CRUD memory, chat delete, Bright Data paid gate')
