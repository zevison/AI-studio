"""V3.9.8.5 no-network integration; mocked paid provider; verify no unauthorized paid requests."""
import json,sys,tempfile,time,threading,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,hot_radar,product_memory,model_pricing,openlux,secrets_store
from app import Handler
from http.server import ThreadingHTTPServer

def main():
 calls=[]
 def fake(method,path,payload=None,**kwargs):
  calls.append((method,path))
  if method=='GET' and path=='/api/pricing':return {'data':[{'model_name':'gpt-6.1-sol','quota_type':0,'input_price_per_m':2,'output_price_per_m':10,'enable_groups':['default']}],'group_ratio':{'default':1}}
  if method=='GET' and path=='/v1/models':return {'data':[{'id':'gpt-6.1-sol'}]}
  if method=='POST' and path=='/v1/chat/completions':return {'choices':[{'message':{'content':json.dumps({
   'product_name_cn':'AIR3 露营充气泵','summary_cn':'来自说明书的产品概况','audiences_cn':['露营用户'],
   'selling_points_cn':['小型便携'],'keyword_suggestions_en':['camping air pump','air mattress inflator','car tire pump'],
   'excluded_keywords_en':['car tire pump'],'fact_candidates':[{'text':'可用于露营充气床垫','source_ids':[source_id],'reason_cn':'说明书'}],
   'risks_cn':['不能将此产品宣传为汽车轮胎泵']},ensure_ascii=False)}}], 'usage':{'prompt_tokens':100,'completion_tokens':300}}
  raise AssertionError('意外的外部调用：'+method+' '+path)
 with tempfile.TemporaryDirectory(prefix='seed3985_') as td,patch.object(config,'DB_PATH',Path(td)/'data.db'),patch.object(db,'DB_PATH',Path(td)/'data.db'),patch.object(config,'DATA_DIR',Path(td)),patch.object(product_memory,'SOURCE_DIR',Path(td)/'knowledge'),patch.object(openlux,'request_json',side_effect=fake),patch.object(secrets_store,'get',return_value='mock-test-key'):
  db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
  base=f'http://127.0.0.1:{server.server_address[1]}'
  def req(path,payload=None):
   request=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'} if payload is not None else {},method='POST' if payload is not None else 'GET')
   try:
    with urllib.request.urlopen(request,timeout=12) as r:return json.load(r)['data']
   except urllib.error.HTTPError as e:
    d=json.load(e); raise ValueError(str(d.get('error') or d))
  try:
   prod=req('/api/product-memory/new',{});pid=prod['id'];assert prod['is_draft']
   blank=req('/api/product-memory/state?product_id='+pid);assert blank['radar_status']=={'items':0,'scripts':0}
   # Saving draft without explicit name does not produce an invalid record.
   draft=req('/api/hot-radar/product/save',{'id':pid,'name':'新商品（待识别）','keywords':[],'facts':[],'restrictions':[],'active':True});assert draft['is_draft'] and not draft['active']
   src=req('/api/product-memory/source/text',{'product_id':pid,'title':'说明书','text':'A portable camping air pump for inflating air mattresses. Not made for tires.'});global source_id;source_id=src['id']
   req('/api/hot-radar/options/save',{'product_model':'gpt-6.1-sol','daily_llm_usd_limit':1.5})
   before=len([c for c in calls if c[0]=='POST']);r=req('/api/product-memory/analyze/preview',{'product_id':pid,'source_ids':[source_id],'model':'gpt-6.1-sol'});assert r['estimated_cost_usd']>0;assert len([c for c in calls if c[0]=='POST'])==before
   try:req('/api/product-memory/analyze/generate',{'product_id':pid,'source_ids':[source_id],'model':'gpt-6.1-sol'})
   except ValueError as e:assert '确认' in str(e)
   else:raise AssertionError('未经授权调用没有被拦截')
   result=req('/api/product-memory/analyze/generate',{'product_id':pid,'source_ids':[source_id],'model':'gpt-6.1-sol','request_hash':r['request_hash'],'state_hash':r['state_hash'],'approve_paid':True,'max_cost_usd':r['estimated_cost_usd']*1.25})
   assert result['status']=='running'
   import time
   for _ in range(100):
    state0=req('/api/product-memory/analyze/status?task_id='+result['id'])
    if state0['status']!='running':break
    time.sleep(.05)
   assert state0['status']=='completed',state0
   check=req('/api/product-memory/state?product_id='+pid);p=next(q for q in check['products'] if q['id']==pid)
   assert 'camping air pump' in p['keywords'] and 'car tire pump' not in p['keywords']
   assert check['memory_version']==1 and len(p['facts'])==1 and p['facts'][0]['status']=='unverified'
   assert check['radar_status']['items']==0
   req('/api/product-memory/fact/status',{'product_id':pid,'text':p['facts'][0]['text'],'status':'verified'})
   manual=req('/api/hot-radar/item/import',{'product_id':pid,'kind':'video','url':'https://www.tiktok.com/@demo/video/123456789','title':'Camping air mattress demonstration','summary':'A product-use trend'})
   check=req('/api/product-memory/state?product_id='+pid);assert check['radar_status']['items']==1 and check['memory_version']==1
   try:
    req('/api/hot-radar/scripts/preview',{'product_id':pid,'source_ids':[manual['id']],'model':'gpt-6.1-sol'})
   except ValueError as e:assert '真实媒体文件' in str(e) or '真实视频' in str(e)
   else:raise AssertionError('未绑定视频禁止付费 GPT 拆解')
   try:req('/api/product-memory/analyze/generate',{'product_id':pid,'source_ids':[source_id],'model':'gpt-6.1-sol','request_hash':r['request_hash'],'state_hash':r['state_hash'],'approve_paid':True,'max_cost_usd':r['estimated_cost_usd']*1.25})
   except ValueError as e:assert '资料或参数已变化' in str(e)
   else:raise AssertionError('重复付费没有阻止')
   refreshed=req('/api/product-memory/analyze/preview',{'product_id':pid,'source_ids':[source_id],'model':'gpt-6.1-sol'});assert refreshed['request_hash']==r['request_hash']
   try:req('/api/product-memory/analyze/generate',{'product_id':pid,'source_ids':[source_id],'model':'gpt-6.1-sol','request_hash':refreshed['request_hash'],'state_hash':refreshed['state_hash'],'approve_paid':True,'max_cost_usd':refreshed['estimated_cost_usd']*1.25})
   except ValueError as e:assert '相同商品资料' in str(e)
   else:raise AssertionError('事实审批后重复付费请求应拒绝')
   assert len([c for c in calls if c[0]=='POST'])==1, calls
   print('PASS: API draft save, price preflight, no implicit paid call, authorized one-time GPT, keyword exclusions, unverified facts, approval, radar evidence, script preflight, duplicate paid firewall')
  finally:server.shutdown();server.server_close();worker.join(3)
if __name__=='__main__':main()
