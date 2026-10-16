# coding: utf-8
"""Pilot V2.5 visual-model catalog audit integrated with Studio's own OpenLux account.
Only GET /v1/models; visual eligibility and actual pricing still require explicit preflight.
"""
from . import openlux
PILOT_MODELS=(
 ('gpt-6-astra','GPT-6 Astra｜深度视觉分析'),
 ('gpt-5.6-sol','GPT-5.6 Sol｜视觉分析'),
 ('gpt-5.5','GPT-5.5｜兼容视觉分析'),
 ('gpt-4o','GPT-4o｜旧版兼容'),
 ('gpt-6.1-sol','GPT-6.1 Sol｜工作台原有默认'),
)
def catalog():
    response=openlux.request_json('GET','/v1/models',timeout=15,retries=0)
    if not isinstance(response,dict) or not isinstance(response.get('data'),list):
        raise ValueError('OpenLux模型目录格式异常，未确认模型可用性')
    names={x['id'] for x in response['data'] if isinstance(x,dict) and isinstance(x.get('id'),str)}
    return {'models':[{'id':k,'name':v,'listed':k in names} for k,v in PILOT_MODELS],
            'checked_with':'GET /v1/models','charged_calls':0,
            'note':'目录可见不等于模型支持视频关键帧，也不等于已通过真实GPT付费分析；付费前仍须核价。'}
