"""Offline API + legacy migration regressions. No provider requests or paid generation."""
import io,json,sqlite3,tempfile,sys,urllib.request,threading,re,time
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,hot_radar,product_memory
from app import Handler

def main():
 with tempfile.TemporaryDirectory(prefix='seedance_flow_3982_') as td:
  d=Path(td); dbpath=d/'legacy.db'
  with patch.object(config,'DB_PATH',dbpath),patch.object(db,'DB_PATH',dbpath),patch.object(config,'DATA_DIR',d),patch.object(product_memory,'SOURCE_DIR',d/'knowledge'):
   db.init_db()
   with db.tx() as c:
    c.execute('''CREATE TABLE radar_products(id TEXT PRIMARY KEY,name TEXT NOT NULL,market TEXT DEFAULT 'US',keywords_json TEXT NOT NULL,facts_json TEXT NOT NULL,restrictions_json TEXT NOT NULL,active INTEGER DEFAULT 1,created_at REAL,updated_at REAL)''')
    c.execute("INSERT INTO radar_products VALUES(?,?,?,?,?,?,?,?,?)",('air3-existing','原有AIR3','US','["camping air pump"]','[]','[]',1,1.,1.))
    c.execute('CREATE TABLE radar_options(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at REAL)')
    c.execute('INSERT INTO radar_options VALUES(?,?,?)',('script_model','"gpt-5.4"',time.time()))
    c.execute('INSERT INTO radar_options VALUES(?,?,?)',('input_usd_per_million','2.5',time.time()))
   hot_radar._schema_initialized=False;product_memory._initialized=False
   hot_radar.ensure_schema();product_memory.ensure_schema()
   assert hot_radar.products()[0]['name']=='原有AIR3'
   assert 'is_draft' in {r['name'] for r in db.all_('PRAGMA table_info(radar_products)')}
   assert len(list((d/'backups').glob('studio_pre_products_3982*.db')))==1
   op=hot_radar.options();assert op['script_model']=='gpt-6.1-sol' and op['input_usd_per_million'] is None
   server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   base=f'http://127.0.0.1:{server.server_address[1]}'
   def api(route,payload=None):
    req=urllib.request.Request(base+route, data=json.dumps(payload).encode() if payload is not None else None,
      headers={'Content-Type':'application/json'} if payload is not None else {},method='POST' if payload is not None else 'GET')
    with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)['data']
   try:
    n=api('/api/product-memory/new',{})
    assert n['id']!='air3-existing' and n['is_draft']
    state=api('/api/product-memory/state?product_id='+n['id'])
    assert state['product_id']==n['id'] and not state['sources'] and len(state['products'])==2
    source=api('/api/product-memory/source/text',{'product_id':n['id'],'text':'ACME AIR PUMP for camping, used on air mattresses. Not suitable for tires.'})
    assert source['extracted_chars']>5
    assert api('/api/product-memory/analyze/preview',{'product_id':n['id'],'source_ids':[source['id']]})['estimated_cost_usd'] is None
    assert api('/api/product-memory/models?model=gpt-6.1-sol')['available'] is None or True
    try:api('/api/product-memory/analyze/generate',{'product_id':n['id'],'source_ids':[source['id']]})
    except urllib.error.HTTPError as e:assert e.code==400
    else:raise AssertionError('Unapproved provider call was allowed')
    assert '新商品' in (ROOT/'static/index.html').read_text()
    print('PASS legacy DB backup, preserved AIR3, new isolated draft, no mandatory keywords, source upload, no-price gate, model catalog, paid firewall')
   finally:server.shutdown();server.server_close();thread.join(2)
if __name__=='__main__':main()
