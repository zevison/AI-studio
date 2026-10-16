"""Dependency-free UI integrity smoke test. No networking, model calls or user data."""
from pathlib import Path
from html.parser import HTMLParser
ROOT=Path(__file__).resolve().parents[1]
class IDs(HTMLParser):
    def __init__(self):super().__init__();self.ids=[]
    def handle_starttag(self,tag,attrs):
        for name,val in attrs:
            if name=='id':self.ids.append(val)

def run():
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    ids=IDs();ids.feed(html)
    assert len(ids.ids)==len(set(ids.ids)), 'duplicate DOM IDs'
    for section in ('video','replica','image','assistant','products','radar','ads','assets','history','settings'):
        assert 'id="page-'+section+'"' in html,section
    for required in ('radarProduct','radarItems','radarGenerateScript','bdSubmit','replicaVideo','replicaInstruction','generateReplica','aiPrompt','aiSend','pmProduct','imagePrompt','generateImage','videoPrompt','previewVideo','adsJobs','taskCenterNav'):
        assert required in ids.ids,required
    for name in ('v42_ui.css','v42_ui.js'):
        assert name in html and (ROOT/'static'/name).is_file()
    js=(ROOT/'static/v42_ui.js').read_text(encoding='utf-8')
    assert 'fetch(' not in js and 'POST' not in js,'UI layer must not initiate paid calls'
    assert 'replaceWith' in js and '.replica-reference-v33' in js
    print('PASS: 10 menus, all critical IDs, no duplicate DOM IDs, no network/paid operations in UI module')
if __name__=='__main__':run()
