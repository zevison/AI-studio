"""V3.9.8.21 no-network product save/catalog/image regression."""
from __future__ import annotations
import base64,json,sys,tempfile,threading,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import db,config,hot_radar,product_memory
from app import Handler

def run():
 with tempfile.TemporaryDirectory(prefix='product21_') as td,patch.object(config,'DB_PATH',Path(td)/'data.db'),patch.object(db,'DB_PATH',Path(td)/'data.db'),patch.object(config,'DATA_DIR',Path(td)),patch.object(product_memory,'SOURCE_DIR',Path(td)/'knowledge'):
  db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  w=threading.Thread(target=server.serve_forever,daemon=True);w.start()
  base=f'http://127.0.0.1:{server.server_address[1]}'
  def api(path,data=None,raw=False):
   req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'} if data is not None else {},method='POST' if data is not None else 'GET')
   with urllib.request.urlopen(req,timeout=10) as r:return r.read() if raw else json.load(r)['data']
  try:
   assert api('/api/product-memory/catalog')==[]
   prod=api('/api/product-memory/new',{});pid=prod['id']
   kw=['Very Long Keyword '*5,'camping air pump','camping air pump']+['keyword '+str(n) for n in range(28)]
   facts=[{'status':'verified','text':'可用于帐篷气垫充气','source':'产品说明书'}, {'status':'unverified','text':'视觉美观','source':'拍摄素材'}]
   saved=api('/api/hot-radar/product/save',{'id':pid,'name':' ETENWOLF AIR3 ','keywords':kw,'facts':facts,'restrictions':['汽车轮胎','汽车轮胎'],'active':True})
   assert saved['name']=='ETENWOLF AIR3' and len(saved['keywords'])==16
   assert max(map(len,saved['keywords']))<=60
   assert saved['facts'][0]['status']=='verified'
   st=api('/api/product-memory/state?product_id='+pid)
   load=next(p for p in st['products'] if p['id']==pid)
   keys=['id','name','active','keywords','facts','restrictions']
   assert {k:saved[k] for k in keys}=={k:load[k] for k in keys}
   catalog=api('/api/product-memory/catalog');assert len(catalog)==1,catalog
   assert catalog[0]['keyword_count']==16 and catalog[0]['verified_count']==1
   assert catalog[0]['memory_version']==0 and catalog[0]['source_count']==0
   # A tiny valid PNG - the image serves from the product folder, not a guessed public path.
   img=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9XDxJvwAAAAASUVORK5CYII=')
   try:
    from PIL import Image
    import io
    with Image.open(io.BytesIO(img)) as image:image.verify()
   except Exception:
    # The ingestion validator is Pillow-dependent; no external services needed.
    from PIL import Image
    import io
    buf=io.BytesIO();Image.new('RGB',(2,2),(255,255,255)).save(buf,format='PNG');img=buf.getvalue()
   uploaded=api('/api/product-memory/source/upload',{'product_id':pid,'name':'air3.png','data_base64':base64.b64encode(img).decode()})
   assert not uploaded['duplicate']
   thumb=api('/api/product-memory/thumbnail?product_id='+pid,raw=True)
   assert thumb==img
   catalog=api('/api/product-memory/catalog')
   assert catalog[0]['source_count']==1 and catalog[0]['image_count']==1
   # Update must not wipe sources/knowledge; no duplicate product created.
   updated=api('/api/hot-radar/product/save',{'id':pid,'name':'ETENWOLF AIR3 新名称','keywords':['camping air pump'],'facts':facts,'restrictions':[],'active':True})
   assert len(api('/api/product-memory/catalog'))==1
   assert api('/api/product-memory/catalog')[0]['source_count']==1
   assert updated['name']=='ETENWOLF AIR3 新名称'
   print('PASS: normalized save/readback, 16 keyword cap, fact equality, separate editable product catalog, persisted thumbnail, no duplicates')
  finally:server.shutdown();server.server_close();w.join(3)
if __name__=='__main__':run()
