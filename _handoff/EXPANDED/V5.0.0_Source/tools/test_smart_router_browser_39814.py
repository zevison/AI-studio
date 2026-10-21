"""Browser DOM smoke test of task-intent, routing mode and manual-override controls. Offline."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]

def main():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  try:
   page=browser.new_page(viewport={'width':1360,'height':900})
   page.set_content((ROOT/'static/index.html').read_text(encoding='utf8'))
   assert page.locator('#videoTaskType').count()==1
   assert page.locator('#videoRoutingMode').count()==1
   assert page.locator('#replicaRoutingMode').count()==1
   assert page.locator('#videoModelChoice').count()==1
   assert page.locator('#replicaModelChoice').count()==1
   assert page.locator('#videoTaskType').input_value()=='auto'
   assert page.locator('#videoRoutingMode').input_value()=='auto'
   page.locator('#videoTaskType').select_option('bulk')
   page.locator('#videoRoutingMode').select_option('cheap')
   manual=page.locator('#videoModelChoice')
   manual.evaluate('(el)=>el.closest("details").open=true')
   manual.select_option('viduq3-turbo')
   assert manual.input_value()=='viduq3-turbo'
   assert page.locator('#setVideoModelChoice').count()==1
   print('PASS browser: task type selector, 3 routing modes, advanced model override and existing global setting')
  finally:browser.close()
if __name__=='__main__':main()
