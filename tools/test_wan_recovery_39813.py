"""No-paid offline checks: Wan 3.0 fail-closed, account lookup and manual GPT reconciliation."""
import os,sys,tempfile,datetime
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R))
from core import video_adapters as v,db,config,task_observability as obs,product_memory as pm,hot_radar as radar

def run():
 assert v.canonical('wan-3.0')=='wan3.0-video'
 assert v.canonical('wan3.0-video')=='wan3.0-video'
 c=v.capabilities('wan3.0-video')
 assert c['create'] is None and c['query'] is None and not c['account_ready']
 d=v.model_diagnostic('wan-3.0',list_fn=lambda:['viduq3-turbo','wan3.0-video'])
 assert d['available'] is True and '路由尚未证实' in d['note'],d
 for mode,res,duration,media in [('text','720p',5,[]),('first_frame','720p',5,[{'kind':'image'}])]:
  try:v.route('wan3.0-video',mode,'9:16',res,duration,media)
  except v.RouteError as exc: assert '不能生成付费预览' in str(exc)
  else:raise AssertionError('Wan preview must fail closed until OpenLux-specific provider contract is verified')
 try:v.create('wan3.0-video','test',[], '9:16','720p',5,False,None)
 except v.RouteError as exc:assert '绝不提交' in str(exc)
 else:raise AssertionError('Unauthorized WAN paid create must fail')
 with tempfile.TemporaryDirectory() as temp,patch.object(db,'DB_PATH',Path(temp)/'studio.db'),patch.object(config,'DB_PATH',Path(temp)/'studio.db'):
  db.init_db();radar._schema_initialized=False;radar.ensure_schema();pm.ensure_schema();obs._initialized=False;obs.ensure_schema()
  product=radar.create_draft();pid=product['id']
  task_id='pa_test_reconcile'
  with db.tx() as conn:
   conn.execute('INSERT INTO product_analysis_runs(id,product_id,request_hash,model,status,estimated_usd,created_at,updated_at,error) VALUES(?,?,?,?,?,?,?,?,?)',(task_id,pid,'hash','gpt-6.1-sol','submission_unknown',0.25,datetime.datetime.now().timestamp(),datetime.datetime.now().timestamp(),'The read operation timed out'))
  try:obs.reconcile_product({'task_id':task_id,'billing_result':'no_charge','confirmed':True,'confirmation':'not sure'})
  except ValueError:pass
  else:raise AssertionError('Reconciliation requires explicit acknowledgement')
  checked=obs.reconcile_product({'task_id':task_id,'billing_result':'no_charge','confirmed':True,'confirmation':'已核对OpenLux账单'})
  assert checked['no_paid_calls'] and checked['status']=='reconciled_no_charge'
  assert db.one('SELECT status FROM product_analysis_runs WHERE id=?',(task_id,))['status']=='reconciled_no_charge'
  assert obs.overview()['tasks'][0]['status']=='reconciled_no_charge'
  assert obs.diagnostics()['tasks'][0]['safe_to_auto_resubmit'] is False
  try:obs.reconcile_product({'task_id':task_id,'billing_result':'charged','confirmed':True,'confirmation':'已核对OpenLux账单'})
  except ValueError:pass
  else:raise AssertionError('Cannot reconcile twice')
 print('PASS: Wan model ID & bounds, free diagnostic, paid preview/create blocked, no GPT duplicate retry, local reconciliation and diagnostic')
if __name__=='__main__':run()
