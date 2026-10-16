"""Offline regression test for V3.9.8.1. NEVER submits real GPT/video API requests."""
import base64, io, json, sys, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import config, db, hot_radar, product_memory

def main():
  with tempfile.TemporaryDirectory(prefix='seedance_product_test_') as temp:
    root=Path(temp);config.DB_PATH=root/'studio.db';db.DB_PATH=config.DB_PATH;config.DATA_DIR=root
    product_memory.SOURCE_DIR=root/'knowledge'
    db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False
    product_memory.ensure_schema()
    p=hot_radar.save_product({'name':'AIR3 测试商品','keywords':['camping air pump'],'restrictions':['不适用于汽车轮胎'],'active':True});pid=p['id']; assert pid
    uploaded=product_memory.add_upload({'product_id':pid,'name':'test.txt','data_base64':base64.b64encode('2600 mAh battery. Camping mattress use.'.encode()).decode()})
    assert uploaded['extracted_chars']>5
    dupe=product_memory.add_upload({'product_id':pid,'name':'test.txt','data_base64':base64.b64encode('2600 mAh battery. Camping mattress use.'.encode()).decode()})
    assert dupe['duplicate'] is True
    pasted=product_memory.add_text({'product_id':pid,'title':'官方资料摘录','text':'AIR3不适用于汽车轮胎充气。'})
    assert pasted['extracted_chars']>5
    try: product_memory._fetch_html('http://127.0.0.1/private')
    except ValueError:pass
    else:raise AssertionError('SSRF guard failed')
    try:product_memory._fetch_html('https://127.0.0.1/private')
    except ValueError:pass
    else:raise AssertionError('IP guard failed')
    try:
      from PIL import Image
      img=Image.new('RGB',(40,40),'white');f=io.BytesIO();img.save(f,format='PNG')
      picture=product_memory.add_upload({'product_id':pid,'name':'air3.png','data_base64':base64.b64encode(f.getvalue()).decode()})
      image_obj=product_memory.image_as_asset({'product_id':pid,'source_id':picture['id']})
      assert image_obj['data'].startswith(b'\x89PNG')
    except ImportError:picture=None
    all_ids=[s['id'] for s in product_memory.sources(pid) if s['kind']!='image']
    no_price=product_memory.preview({'product_id':pid,'source_ids':all_ids,'model':'gpt-6.1-sol'})
    assert no_price['estimated_cost_usd'] is None
    try:product_memory.analyze({'product_id':pid,'source_ids':all_ids,'approve_paid':False})
    except ValueError:pass
    else:raise AssertionError('Payment gate failed')
    hot_radar.save_options({'product_model':'gpt-6.1-sol','daily_llm_usd_limit':2})
    pl={'product_id':pid,'source_ids':all_ids,'model':'gpt-6.1-sol','analyze_images':False}
    from core import model_pricing
    pricing={'available':True,'input_usd_per_million':2,'output_usd_per_million':10,'warning':'MOCK prices for offline test'}
    model_patch=patch.object(model_pricing,'get',return_value=pricing)
    model_patch.start()
    q=product_memory.preview(pl)
    assert q['estimated_cost_usd']>0
    fake={'choices':[{'message':{'content':json.dumps({'product_name_cn':'AIR3 露营气泵','summary_cn':'露营气泵','audiences_cn':['露营人群'],
           'selling_points_cn':['便携候选'], 'keyword_suggestions_en':['portable air pump'],
           'risks_cn':['需核实'], 'fact_candidates':[{'text':'支持充气床垫','source_ids':[all_ids[0]],'reason_cn':'来源声称支持'}]})}}], 'usage':{'total_tokens':123}}
    with patch.object(product_memory.secrets_store,'get',return_value='mock-key'),patch.object(product_memory.openlux,'request_json',return_value=fake) as send:
      result=product_memory.analyze({**pl,'request_hash':q['request_hash'],'state_hash':q['state_hash'],'approve_paid':True,'price_confirmed':True,'max_cost_usd':q['estimated_cost_usd']*1.3})
      assert result['status']=='completed' and send.call_count==1
      snap=product_memory.state(pid)
      assert snap['memory_version']==1
      assert 'portable air pump' in next(z for z in snap['products'] if z['id']==pid)['keywords']
      added=[x for x in snap['products'][0]['facts'] if x['text']=='支持充气床垫']
      assert len(added)==1 and added[0]['status']=='unverified'
      try:product_memory.analyze({**pl,'request_hash':q['request_hash'],'state_hash':q['state_hash'],'approve_paid':True,'price_confirmed':True,'max_cost_usd':q['estimated_cost_usd']*1.3})
      except ValueError:pass
      else:raise AssertionError('Duplicate payment guard failed')
    product_memory.change_fact({'product_id':pid,'text':'支持充气床垫','status':'verified'})
    assert any(x['status']=='verified' and x['text']=='支持充气床垫' for x in hot_radar.products()[0]['facts'])
    assert len(product_memory.sources(pid))>=2
    model_patch.stop()
    print('PASS: initial schema, upload/dedup, pasted evidence, private-IP block, image asset, cost gate, no approval, GPT mock, unverified facts, duplicate guard, human verification')
if __name__=='__main__':main()
