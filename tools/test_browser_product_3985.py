"""Headless Chromium smoke test: clickable buttons, save, no silent disabled analysis, full post-analysis flow."""
import json,sys,threading,tempfile,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,hot_radar,product_memory,openlux,secrets_store
from app import Handler

def test():
 calls=[]
 def provider(method,path,payload=None,**kwargs):
  calls.append((method,path))
  if path=='/api/pricing':return {'data':[{'model_name':'gpt-6.1-sol','quota_type':0,'input_price_per_m':2,'output_price_per_m':10,'enable_groups':['default']}],'group_ratio':{'default':1}}
  if path=='/v1/models':return {'data':[{'id':'gpt-6.1-sol'}]}
  if path=='/v1/chat/completions':
   content=json.loads(payload['messages'][-1]['content'] if isinstance(payload['messages'][-1]['content'],str) else '{}')
   sources=content.get('sources') or []
   sid=sources[0]['id']
   doc={'product_name_cn':'露营充气泵','summary_cn':'根据商品资料描述','audiences_cn':['露营者'], 'selling_points_cn':['便携'],
   'keyword_suggestions_en':['portable camping air pump','car tire inflator'], 'excluded_keywords_en':['car tire inflator'], 'risks_cn':['勿展示轮胎充气'],
   'fact_candidates':[{'text':'适用充气床垫','source_ids':[sid],'reason_cn':'资料提及'}]}
   return {'choices':[{'message':{'content':json.dumps(doc,ensure_ascii=False)}}], 'usage':{'total_tokens':1000}}
  if path=='/api/ratio_config':return {'data':{'model_ratio':{},'completion_ratio':{}}}
  return {}
 with tempfile.TemporaryDirectory() as td,patch.object(config,'DB_PATH',Path(td)/'a.db'),patch.object(db,'DB_PATH',Path(td)/'a.db'),patch.object(config,'DATA_DIR',Path(td)),patch.object(product_memory,'SOURCE_DIR',Path(td)/'knowledge'),patch.object(openlux,'request_json',side_effect=provider),patch.object(secrets_store,'get',return_value='fake'):
  db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1280,'height':900})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',lambda d:d.accept())
    def proxy(route):
     original=route.request
     from urllib.parse import urlparse
     url=urlparse(original.url)
     target=f'http://127.0.0.1:{server.server_address[1]}{url.path}'+(('?'+url.query) if url.query else '')
     body=original.post_data.encode() if original.post_data is not None else None
     request=urllib.request.Request(target,data=body,headers={'Content-Type':'application/json'} if body else {},method=original.method)
     try:
      with urllib.request.urlopen(request,timeout=15) as reply:
       route.fulfill(status=reply.status,body=reply.read(),headers={'Content-Type':reply.headers.get('Content-Type','text/plain')})
     except urllib.error.HTTPError as e:
      route.fulfill(status=e.code,body=e.read(),headers={'Content-Type':'application/json'})
    page.route('**/*',proxy)
    page.goto('http://seedance.local/',wait_until='domcontentloaded',timeout=20000)

    page.locator('[data-page="products"]').click()
    page.locator('#pmNew').click();page.locator('#pmCurrentNotice').wait_for(state='visible')
    page.wait_for_function("document.getElementById('pmCurrentNotice').textContent.includes('全新商品')",timeout=5000)
    page.locator('#pmName').fill('测试露营气泵')
    page.locator('#pmSave').click()
    page.wait_for_function("document.getElementById('pmSaveFeedback').textContent.includes('已保存')",timeout=8000)
    assert page.locator('#pmName').input_value()=='测试露营气泵'
    # Click without sources: visible and actionable; must report reason, not silently disable.
    assert page.locator('#pmAnalyze').is_enabled()
    page.locator('#pmAnalyze').click()
    page.wait_for_function("document.getElementById('pmSaveFeedback').textContent.includes('请先上传')",timeout=5000)
    page.locator('#pmTextTitle').fill('真实商品说明')
    page.locator('#pmText').fill('Small portable air pump for camping inflatable mattresses. Not for car tires.')
    page.locator('#pmTextAdd').click()
    page.wait_for_function("document.querySelectorAll('.pm-source-check').length===1",timeout=7000)
    page.locator('#pmDailyBudget').fill('1.5')
    page.locator('#pmPreview').click()
    page.wait_for_function("document.getElementById('pmEstimate').textContent.includes('预计费用')",timeout=8000)
    assert page.locator('#pmAnalyze').is_enabled(), 'the analyze button must remain actionable'
    page.locator('#pmAnalyze').click()
    page.wait_for_function("document.getElementById('pmSummary').textContent.includes('商品资料')",timeout=15000)
    assert 'portable camping air pump' in page.locator('#pmKeywords').input_value()
    assert 'car tire inflator' not in page.locator('#pmKeywords').input_value()
    assert page.locator('#pmWorkflow .pm-step').count()==6
    assert page.locator('#pmScanNow').is_enabled()
    assert '分析已保存' in page.locator('#pmSaveFeedback').inner_text()
    page.reload(wait_until='domcontentloaded')
    page.locator('[data-page="products"]').click()
    page.wait_for_function("document.getElementById('pmKeywords').value.includes('portable camping air pump')",timeout=10000)
    assert '测试露营气泵' in page.locator('#pmProduct').inner_text()
    assert not errors,'JS errors: '+str(errors)
    print('PASS Chromium user flow: new product, save/readback, analyze button error, document source, quote preview, explicit paid approval, GPT result, keywords, next steps, reload persistence')
    browser.close()
  finally:server.shutdown();server.server_close();thread.join(4)
if __name__=='__main__':test()
