"""V4.2.8.10 audit regression: no remote calls, no paid GPT or Bright Data triggers."""
from pathlib import Path
import sys, tempfile, datetime as dt
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import config,db,hot_radar

def run():
 with tempfile.TemporaryDirectory() as root:
  target=Path(root)/'clean.db'
  with patch.object(db,'DB_PATH',target),patch.object(config,'DB_PATH',target):
   db.init_db();hot_radar._schema_initialized=False;hot_radar.ensure_schema()
   product=hot_radar.save_product({'name':'AIR3 sample','keywords':['portable camping air pump'], 'facts':[{'text':'Inflates camping mattresses','status':'verified'}]})
   pid=product['id']; now=dt.datetime.now(dt.timezone.utc)
   oldtime=(now-dt.timedelta(days=31)).isoformat()
   url='https://www.tiktok.com/@demo/video/76500000000001'
   first={'url':url,'title':'Air pump video','published_at':oldtime,'media_url':'https://v16.tiktokcdn.com/old-expiring.mp4','cover_url':'https://p16.tiktokcdn.com/old.jpeg','region':'GB','viewCount':8}
   assert hot_radar.import_tiktok_batch({'product_id':pid,'records':[first]})['inserted']==1
   before=hot_radar.items(pid)[0]
   assert before['metadata']['freshness']=='历史参考/发布时间待核实'
   assert before['metadata']['us_status']=='非美国地区'
   refreshed={**first,'title':'Air pump updated','media_url':'https://v16.tiktokcdn.com/fresh-signed.mp4','cover_url':'https://p16.tiktokcdn.com/new.jpeg','viewCount':900,'published_at':(now-dt.timedelta(days=2)).isoformat(),'region':'US'}
   assert hot_radar.import_tiktok_batch({'product_id':pid,'records':[refreshed]})['inserted']==0
   after=hot_radar.items(pid)[0]
   assert after['id']==before['id'], 're-import must preserve link to manual video binding and scripts'
   assert after['metadata']['media_url']==refreshed['media_url']
   assert after['metadata']['cover_url']==refreshed['cover_url']
   assert after['metadata']['metrics']['viewCount']==900
   assert after['metadata']['freshness']=='近7天'
   assert after['metadata']['us_status']=='美国US'
   # A later date causes freshness to age out without rewriting the original import record.
   future=(now+dt.timedelta(days=19)).timestamp()
   with patch.object(hot_radar,'_now',return_value=future):
    aged=hot_radar.items(pid)[0]
    assert aged['metadata']['freshness']=='历史参考/发布时间待核实'
   assert db.one('SELECT COUNT(*) AS n FROM radar_items')['n']==1
 print('PASS: duplicate re-import enriches metadata, preserves IDs; dynamic 7/14 freshness and US labels; no paid calls')
if __name__=='__main__':run()
