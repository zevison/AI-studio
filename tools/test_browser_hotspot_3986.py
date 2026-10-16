"""Isolated headless browser UI test with fake HTTP responses; no network/paid requests."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'static/index.html').read_text(encoding='utf8')
SECTION=HTML.split('<section class="page" id="page-ads">',1)[1].split('<section class="page active" id="page-video">',1)[0]
MOCK_JS=r'''() => {
window.toast=(s,b)=>window.message=s;
window.activatePage=(page)=>window.activePage=page;
window.confirm=()=>true;
let doc={market_country:'US',as_of_us:'2026-10-12',season:'fall',seasonal_fit_cn:'US autumn',title_cn:'AIR3',hook_en:'Camping can be easy.',hook_zh:'露营可以很轻松。',script_segments:[{duration_s:5,narration_en:'Pack your pump.',narration_zh:'带好气泵。',visual_cn:'真实产品特写'}]};
window.demo={doc,job:null};
window.fetch=async(url,options={})=>{
let q=url.split('?')[0],payload=options.body?JSON.parse(options.body):{},d=window.demo,result;
if(q==='/api/models')result={effective:'minimax-video-h3'};
else if(q==='/api/video-model-tests/plan')result={candidates:[],test_cases:[],target_resolution:'720P',ratio:'9:16',total_per_model:6};
else if(q==='/api/hot-radar/state')result={scripts:[{id:'demo-script',title:'AIR3',model:'gpt-6.1-sol',status:'completed'}]};
else if(q==='/api/creative-production/state')result=d.job?[d.job]:[];
else if(q==='/api/creative-production/new')result=(d.job={id:'job1',state:'await_script_approval',edited_script:doc,model:'minimax-video-h3',product_id:'p1'});
else if(q==='/api/creative-production/script/approve')result=(d.job={...d.job,state:'await_h3_test',edited_script:payload.script});
else if(q==='/api/creative-production/concept/approve')result=(d.job={...d.job,state:'await_h3_test'});
else if(q==='/api/creative-production/model/activate')result={selected_model:'minimax-video-h3',effective_model:'minimax-video-h3',paid_request_submitted:false};
else if(q==='/api/creative-production/h3/handoff')result={id:'job1',model:'minimax-video-h3',video_prompt:'Camping air pump 9:16 only real features.'};
else throw Error('Unexpected API '+q);
return {ok:true,status:200,json:async()=>({ok:true,data:result})};
};
}'''

def main():
 errors=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=browser.new_page()
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.set_content('<html><body><section id="page-ads">'+SECTION+'</section><textarea id="videoPrompt"></textarea></body></html>')
  page.evaluate(MOCK_JS)
  page.add_script_tag(content=(ROOT/'static/creative_production.js').read_text(encoding='utf8'))
  page.evaluate('window.CreativeProduction.load()')
  page.wait_for_function("document.querySelectorAll('#adsScriptSelect option').length>0",timeout=4000)
  page.locator('#adsNewJob').click()
  page.locator('#adsApproveScript').wait_for(timeout=4000)
  page.locator('#adsApproveScript').click()
  page.locator('#adsModelHandoff').wait_for(timeout=4000)
  page.locator('#adsModelHandoff').click()
  page.wait_for_function('document.getElementById("videoPrompt").value.includes("Camping")',timeout=4000)
  assert page.evaluate('window.activePage')=='video'
  assert not errors,errors
  browser.close()
 print('PASS: browser click flow script-confirm -> H3 handoff; no JS errors or real API calls')
if __name__=='__main__':main()
