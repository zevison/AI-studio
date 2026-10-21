"""Browser-only UI test without network: product buttons and displayed next steps."""
from pathlib import Path
import json,re
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]

def run():
 html=(ROOT/'static/index.html').read_text(encoding='utf-8')
 # No external network, no unrelated workspace scripts.
 start=html.index('<section class="page" id="page-products">')
 end=html.index('<section class="page" id="page-radar">')
 product=html[start:end]
 mini='<!doctype html><html><head><meta charset="UTF-8" /></head><body>'+product+'<textarea id="videoPrompt"></textarea></body></html>'
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=browser.new_page()
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.set_content(mini)
  page.evaluate('''() => {
    window.toast=(m,bad)=>{window.lastToast=m;};
    window.activatePage=(p)=>{window.activePage=p;};
    window.confirm=()=>true;
    window.mock={products:[],sources:[],options:{product_model:'gpt-6.1-sol',daily_llm_usd_limit:1}, memory:{},history:[],radar_status:{items:0,scripts:0}};
    window.fetch=async (url,opt={})=>{
      let q=url.split('?')[0], body=opt.body?JSON.parse(opt.body):{};let data={};const m=window.mock;
      const product=()=>m.products.find(p=>p.id===body.id||p.id===body.product_id)||m.products[0];
      if(q==='/api/product-memory/new'){
        let p={id:'p'+(m.products.length+1),name:'新商品（待识别）',is_draft:true,keywords:[],facts:[],restrictions:[],active:false};m.products.unshift(p);data=p;
      }else if(q==='/api/product-memory/state'){
        data={products:m.products,product_id:m.products[0]?.id,sources:m.sources,memory:m.memory,memory_version:m.version||0,history:m.history,radar_status:m.radar_status};
      }else if(q==='/api/hot-radar/state')data={options:m.options};
      else if(q==='/api/model-pricing')data={available:true,input_usd_per_million:2,output_usd_per_million:10,source:'mock',group_policy:'test'};
      else if(q==='/api/hot-radar/options/save'){Object.assign(m.options,body);data=m.options;}
      else if(q==='/api/hot-radar/product/save'){
        let prod=product();Object.assign(prod,{name:body.name,keywords:body.keywords,facts:body.facts,restrictions:body.restrictions,active:!!(body.active&&body.keywords.length),is_draft:body.name==='新商品（待识别）'});data=prod;
      }else if(q==='/api/product-memory/source/text'){
        let s={id:'s1',kind:'text',title:body.title||'测试',text_length:body.text.length,excerpt:body.text,created_at:Date.now()/1000};m.sources.push(s);data=s;
      }else if(q==='/api/product-memory/analyze/preview')data={model:body.model,source_count:body.source_ids.length,images_to_analyze:0,estimated_input_tokens:900,max_output_tokens:3500,estimated_cost_usd:0.05,warning:'mock',request_hash:'hash',state_hash:'state'};
      else if(q==='/api/product-memory/analyze/generate'){
        let prod=product();prod.keywords=['camping air pump'];prod.facts=[{text:'适用充气床垫',source:'资料证据：s1',status:'unverified'}];prod.name='露营气泵';prod.is_draft=false;prod.active=true;
        m.version=1;m.memory={summary_cn:'测试商品资料已完成分析'};data={memory_version:1,status:'completed'};
      }else if(q==='/api/hot-radar/scan'){m.radar_status.items=2;data={inserted:2,status:'completed'};}
      else if(q==='/api/product-memory/fact/status'){product().facts[0].status=body.status;data={updated:true};}
      else if(q==='/api/product-memory/models')data={available:true,message:'test'};
      else throw new Error('unmocked API '+q);
      return {ok:true,status:200,json:async()=>({ok:true,data})};
    };
  }''')
  page.add_script_tag(content=(ROOT/'static/product_memory.js').read_text(encoding='utf-8'))
  page.locator('#pmNew').click()
  page.wait_for_function("document.getElementById('pmCurrentNotice').textContent.includes('全新商品')",timeout=2000)
  page.locator('#pmName').fill('第一件商品')
  page.locator('#pmSave').click()
  page.wait_for_function("document.getElementById('pmSaveFeedback').textContent.includes('已保存')",timeout=2000)
  assert page.locator('#pmAnalyze').is_enabled()
  page.locator('#pmAnalyze').click()
  page.wait_for_function("document.getElementById('pmSaveFeedback').textContent.includes('请先上传')",timeout=2000)
  page.locator('details:has(#pmText) summary').click()
  page.locator('#pmText').fill('Portable electric air pump suitable for air mattresses.')
  page.locator('#pmTextAdd').click()
  page.wait_for_function("document.querySelectorAll('.pm-source-check').length===1",timeout=2000)
  page.locator('#pmPreview').click()
  page.wait_for_function("document.getElementById('pmEstimate').textContent.includes('预计费用')",timeout=2000)
  assert page.locator('#pmAnalyze').is_enabled()
  page.locator('#pmAnalyze').click()
  page.wait_for_function("document.getElementById('pmKeywords').value.includes('camping air pump')",timeout=2000)
  assert page.locator('#pmWorkflow .pm-step').count()==6
  page.locator('#pmScanNow').click()
  page.wait_for_function("document.getElementById('pmSaveFeedback').textContent.includes('新增 2')",timeout=2000)
  assert not errors,errors
  browser.close()
  print('PASS headless browser mocked UI: clickable analysis, visible errors, save, source upload, preview, confirmed GPT action, keywords, radar next-step workflow')
if __name__=='__main__':run()
