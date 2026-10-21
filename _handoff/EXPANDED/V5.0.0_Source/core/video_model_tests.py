"""Read-only model options for TikTok creative tests, not a paid routing service."""
CANDIDATES=[
 {'name':'Vidu Q3 Turbo','role':'低成本首版效果测试','ready':False,'reason':'须核实 OpenLux 实际报价、视频参数与模型路由'},
 {'name':'MiniMax H3','role':'动作效果对照','ready':False,'reason':'须核实实际模型编号与接口参数'},
 {'name':'Seedance 2.5','role':'高要求对照与已有流程','ready':False,'reason':'仍保留旧视频创作能力'}]
TEST_CASES=[{'id':'A','name':'产品轻微运镜','count':2,'duration_seconds':5},
            {'id':'B','name':'露营场景','count':2,'duration_seconds':5},
            {'id':'C','name':'人物手持产品','count':2,'duration_seconds':5}]
def plan():
 return {'candidates':CANDIDATES,'test_cases':TEST_CASES,'ratio':'9:16',
         'target_resolution':'720P','total_per_model':6,'paid_generation_permitted':False,
         'admission':'商品事实、真实模型路由、视频参数、账户费用以及单次人工授权须全部通过',
         'score_dimensions':['产品外观保真','动作自然','场景真实','技术质量','每条合格镜头成本']}
