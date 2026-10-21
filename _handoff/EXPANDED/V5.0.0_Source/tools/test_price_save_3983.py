"""V3.9.8.3 offline contract tests; no real network or paid provider calls."""
import json,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,hot_radar,product_memory,model_pricing,openlux

def run():
    calls=[]
    def fake_provider(method,path,**kwargs):
        calls.append((method,path))
        if method!='GET':raise AssertionError('UNAUTHORIZED PAID CALL')
        if path=='/api/pricing':return {'data':[{'model_name':'gpt-6.1-sol','quota_type':0,'model_ratio':1.0,'completion_ratio':5.0,'enable_groups':['default']}],'group_ratio':{'default':1.0}}
        if path=='/api/ratio_config':return {'data':{'model_ratio':{'gpt-6.1-sol':1.0},'completion_ratio':{'gpt-6.1-sol':5.0}}}
        if path=='/v1/models':return {'data':[{'id':'gpt-6.1-sol'}]}
        raise AssertionError(path)
    with tempfile.TemporaryDirectory(prefix='seed_3983_') as td,patch.object(config,'DB_PATH',Path(td)/'test.db'),patch.object(db,'DB_PATH',Path(td)/'test.db'),patch.object(config,'DATA_DIR',Path(td)),patch.object(product_memory,'SOURCE_DIR',Path(td)/'knowledge'),patch.object(openlux,'request_json',side_effect=fake_provider):
        db.init_db(); hot_radar._schema_initialized=False;product_memory._initialized=False;db.init_db()
        saved=hot_radar.save_options({'product_model':'gpt-6.1-sol','script_model':'gpt-6.1-sol','daily_llm_usd_limit':2.5,'auto_scan':True,'scan_hour_bj':9,'input_usd_per_million':99999})
        assert saved['product_model']=='gpt-6.1-sol' and saved['script_model']=='gpt-6.1-sol'
        assert saved['input_usd_per_million'] is None, 'must ignore manual input price'
        assert hot_radar.options()['daily_llm_usd_limit']==2.5
        price=model_pricing.get('gpt-6.1-sol',force=True)
        assert price['available'] and price['input_usd_per_million']==2.0 and price['output_usd_per_million']==10.0,price
        assert not model_pricing.get('unknown-model',force=True)['available'],'unknown model should not have guessed prices'
        prod=hot_radar.create_draft()
        prod=hot_radar.save_product({'id':prod['id'],'name':'AIR3','keywords':['camping air pump'],'facts':[],'restrictions':['no tire inflation'],'active':True})
        assert hot_radar.products()[0]['name']=='AIR3'
        source=product_memory.add_text({'product_id':prod['id'],'title':'说明','text':'Portable air pump for air mattresses and pool floats.'})
        data=product_memory.preview({'product_id':prod['id'],'source_ids':[source['id']],'model':'gpt-6.1-sol','analyze_images':False})
        assert 0<data['estimated_cost_usd']<2.5 and data['price']['available'],data
        try:product_memory.analyze({'product_id':prod['id'],'source_ids':[source['id']],'model':'gpt-6.1-sol'})
        except ValueError as e:assert '确认' in str(e)
        else:raise AssertionError('unapproved paid analysis allowed')
        try:hot_radar.add_manual({'product_id':prod['id'],'kind':'news','url':'https://example.com/camp-trend','title':'Camping trend'})
        except ValueError as e:assert 'TikTok' in str(e)
        else:raise AssertionError('旧新闻雷达应已被停用')
        imported=hot_radar.add_manual({'product_id':prod['id'],'kind':'video','url':'https://www.tiktok.com/@creator/video/12345','title':'Camping demonstration'})
        try:hot_radar.script_preview({'product_id':prod['id'],'source_ids':[imported['id']],'model':'gpt-6.1-sol'})
        except ValueError as e:assert '视频' in str(e)
        else:raise AssertionError('没有实际视频不得生成拆解脚本')
        assert not any(method=='POST' for method,_ in calls)
        print('PASS pricing API parses exact model and output/input; rejects missing price; ignores manual rates; persists selection and budget; product and script preflights; paid firewall')
if __name__=='__main__':run()
