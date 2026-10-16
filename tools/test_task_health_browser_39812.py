"""Isolated Chromium UI check: uses mocked read-only overview (no server/network)."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'static/app.js').read_text(encoding='utf8')
block=source.split('async function refreshTaskHealth(){',1)[1].split('async function refreshTaskCenter(',1)[0]
code='async function refreshTaskHealth(){'+block

def main():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  page=browser.new_page()
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.set_content('<div id="taskHealthRows"></div><button id="taskHealthRefresh">刷新</button><button id="taskHealthCopy">复制</button>')
  page.evaluate('''() => {
    window.$=(sel)=>document.querySelector(sel);
    window.esc=(v)=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.fmtElapsed=(v)=>Math.floor(v)+'秒';window.statusLabel=(v)=>v;
    window.toast=()=>{};
    window.api=async(route)=>({tasks:[{id:'ui-test',kind:'product',status:'submission_unknown',phase:'提交结果未知，待核对',model:'gpt-6.1-sol',elapsed_seconds:105,since_update_seconds:100,stalled:false,error:'网络读取超时',suggested_action:'不能自动付费重试'}],attention_count:1});
    window.navigator.clipboard={writeText:async()=>{}};
  }''')
  page.add_script_tag(content=code+'\nwindow.refreshTaskHealth=refreshTaskHealth;')
  page.evaluate('window.refreshTaskHealth()')
  page.locator('#taskHealthRows').get_by_text('商品 GPT 理解').wait_for(timeout=3000)
  assert '待核对' in page.locator('#taskHealthRows').inner_text()
  page.locator('#taskHealthRefresh').click()
  assert not errors,errors
  browser.close()
 print('PASS browser isolated task-status rendering and manual refresh; no external web or paid model calls')
if __name__=='__main__':main()
