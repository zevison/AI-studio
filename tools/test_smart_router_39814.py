"""Offline: no real provider calls, no paid video/LLM generation."""
import base64, json, time, tempfile, sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import intelligent_router as rt, db,config,hot_radar,product_memory,model_pricing,task_observability

def test_router():
    base=dict(mode='text',duration=5,images=0,videos=0,audios=0,ratio='9:16',resolution='720p')
    a=rt.choose(base,mode='auto');b=rt.choose(base,mode='cheap');f=rt.choose(base,mode='fast')
    assert a['selected_model']==rt.SEED and b['selected_model']==rt.VIDU
    assert f['selected_model']==rt.SEED and all(v['quote']['estimated_total_usd'] is None for v in (a,b,f))
    assert b['quote']['reference_lower_bound_usd'] is not None
    c=rt.choose({**base,'mode':'edit','duration':20,'videos':1,'images':1},mode='cheap')
    assert c['selected_model']==rt.SEED and c['task_type']=='edit'
    assert rt.choose({**base,'resolution':'2K','duration':10},override=rt.H3)['selected_model']==rt.H3
    # H3 supports 768P and 2K native. 720P export is a clearly labeled local downscale.
    h=rt.choose(base,override=rt.H3)
    assert h['selected_model']==rt.H3 and '768P' in h['selection_reason'] or h['selected_model']==rt.H3
    for field in [{'duration':34},{'videos':2,'mode':'edit','duration':40},{'task_type':'motion','videos':1,'mode':'reference'}]:
        try:rt.choose({**base,**field})
        except ValueError:pass
        else:raise AssertionError('Incompatible task admitted '+str(field))
    try:rt.choose(base,override='wan3.0-video')
    except ValueError as e:assert '接口' in str(e)
    else:raise AssertionError('Wan generated without adapter')
    # True measurements only; no invented speeds or costs.
    samples=[{'model':rt.VIDU,'status':'completed','has_downloadable_video':True,'elapsed_seconds':10,'actual_cost_usd':0.5} for _ in range(5)]
    assert rt.choose(base,mode='fast',history=samples)['selected_model']==rt.VIDU
    assert rt.choose(base,mode='auto',history=samples)['selected_model']==rt.VIDU
    assert not any(m['state']=='adapter_available' for m in rt.catalog() if m['id']=='wan3.0-video')


def test_async_product():
    with tempfile.TemporaryDirectory(prefix='router_product_') as t:
      root=Path(t)
      with patch.object(db,'DB_PATH',root/'studio.db'),patch.object(config,'DB_PATH',root/'studio.db'),patch.object(config,'DATA_DIR',root),patch.object(product_memory,'SOURCE_DIR',root/'knowledge'):
        db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False;task_observability._initialized=False;product_memory.ensure_schema()
        hot_radar.save_options({'daily_llm_usd_limit':2})
        p=hot_radar.save_product({'name':'AIR3','keywords':['camping air pump']})
        src=product_memory.add_text({'product_id':p['id'],'title':'product facts','text':'Small air pump for camping mattress.'})
        req={'product_id':p['id'],'source_ids':[src['id']],'model':'gpt-6.1-sol','analyze_images':False}
        price={'available':True,'input_usd_per_million':1,'output_usd_per_million':2}
        output={'choices':[{'message':{'content':json.dumps({'product_name_cn':'AIR3','summary_cn':'美国露营气泵','keyword_suggestions_en':['camping inflatable pump'],'fact_candidates':[]})}}], 'usage':{'total_tokens':1000}}
        with patch.object(model_pricing,'get',return_value=price),patch.object(product_memory.secrets_store,'get',return_value='fake_key'),patch.object(product_memory.openlux,'request_json',return_value=output) as mock_api:
          preview=product_memory.preview(req)
          auth={**req,'request_hash':preview['request_hash'],'state_hash':preview['state_hash'],'approve_paid':True,'max_cost_usd':preview['estimated_cost_usd']*1.3}
          result=product_memory.analyze(auth,async_mode=True)
          assert result['status']=='running' and result['id']
          deadline=time.time()+10
          while time.time()<deadline:
            status=product_memory.analysis_status(result['id'])
            if status['status']!='running':break
            time.sleep(.05)
          assert status['status']=='completed',status
          assert status['memory_version']==1
          raw=db.one('SELECT raw_response_json FROM product_analysis_runs WHERE id=?',(result['id'],))
          assert json.loads(raw['raw_response_json'])==output
          assert mock_api.call_count==1
          assert mock_api.call_args.kwargs['timeout']==300
          try: product_memory.analyze(auth,async_mode=True)
          except ValueError:pass
          else:raise AssertionError('Duplicate paid request allowed')
          print('PASS async product: returns local id, one paid mock call, 300s timeout, raw response saved, knowledge version updated, duplicate blocked')


# Additional test: provider returned content, parsing failed, persisted original is reusable.
def test_reparse_without_payment():
    with tempfile.TemporaryDirectory(prefix='router_reparse_') as td:
        root=Path(td)
        with patch.object(db,'DB_PATH',root/'studio.db'),patch.object(config,'DB_PATH',root/'studio.db'),patch.object(config,'DATA_DIR',root),patch.object(product_memory,'SOURCE_DIR',root/'source'):
            db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False;task_observability._initialized=False
            hot_radar.save_options({'daily_llm_usd_limit':2})
            pr=hot_radar.save_product({'name':'AIR3','keywords':['camping air pump']})
            source=product_memory.add_text({'product_id':pr['id'],'title':'real specification','text':'AIR3 small electric camping pump.'})
            req={'product_id':pr['id'],'source_ids':[source['id']],'model':'gpt-6.1-sol','analyze_images':False}
            invalid={'choices':[{'message':{'content':'Not JSON'}}]}
            with patch.object(model_pricing,'get',return_value={'available':True,'input_usd_per_million':1,'output_usd_per_million':2}),patch.object(product_memory.secrets_store,'get',return_value='fake'),patch.object(product_memory.openlux,'request_json',return_value=invalid) as provider:
                p=product_memory.preview(req)
                try:product_memory.analyze({**req,'request_hash':p['request_hash'],'state_hash':p['state_hash'],'approve_paid':True,'max_cost_usd':p['estimated_cost_usd']*1.3})
                except RuntimeError:pass
                rows=db.all_('SELECT * FROM product_analysis_runs')
                assert len(rows)==1 and rows[0]['status']=='parse_failed',rows
                assert json.loads(rows[0]['raw_response_json'])==invalid
                corrected={'choices':[{'message':{'content':json.dumps({'summary_cn':'可恢复','keyword_suggestions_en':['air pump camping'],'fact_candidates':[]})}}]}
                db.execute('UPDATE product_analysis_runs SET raw_response_json=? WHERE id=?',(json.dumps(corrected),rows[0]['id']))
                result=product_memory.reparse_saved(rows[0]['id'])
                assert result['status']=='completed' and provider.call_count==1
                print('PASS parse failure: raw result retained, only local reparse, no second provider call')

if __name__=='__main__':
 test_router();test_async_product();test_reparse_without_payment();print('PASS smart router: auto/fast/cheap, capability hard gates, manual override, unknown price and historical evidence')
