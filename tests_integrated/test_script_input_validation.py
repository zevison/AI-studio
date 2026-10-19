import pytest
from core import us_marketing,creative_production


def document():
    market=us_marketing.context()
    return {**{k:market[k] for k in ('market_country','as_of_us','season')},'seasonal_fit_cn':'全年适用',
        'hook_en':'A useful product','hook_zh':'实用商品',
        'script_segments':[{'duration_s':20,'narration_en':'Use the product','narration_zh':'使用商品','visual_cn':'商品实拍特写'}]}


@pytest.mark.parametrize('duration',[0,-1,None,True,'bad',float('nan'),float('inf'),[],{},301])
def test_bad_duration_cannot_reach_production(duration):
    doc=document();doc['script_segments'][0]['duration_s']=duration
    result=us_marketing.validate_script(doc,us_marketing.context())
    assert not result['ok'] and any('时长' in e for e in result['errors'])


@pytest.mark.parametrize('segments',[4,True,{},'not a list',None])
def test_malformed_segments_are_a_validation_error_not_crash(segments):
    doc=document();doc['script_segments']=segments
    assert not us_marketing.validate_script(doc,us_marketing.context())['ok']


def test_concept_does_not_silently_shorten_approved_shots():
    doc=document();assert us_marketing.validate_script(doc,us_marketing.context())['ok']
    assert creative_production.concept({'edited_script':doc,'model':'auto'})['scenes'][0]['seconds']==20


def test_silent_video_never_loads_or_downloads_speech_models(monkeypatch,tmp_path):
    from core import hypit_engine
    monkeypatch.setattr(hypit_engine.media,'probe',lambda _: {'ok':True,'has_audio':False})
    def forbidden():raise AssertionError('Silent video must not initialize speech engines')
    monkeypatch.setattr(hypit_engine,'_whisperx_exe',forbidden)
    result=hypit_engine.transcribe_if_available(tmp_path/'silent.mp4',tmp_path/'cache')
    assert not result['used'] and '没有音轨' in result['reason']
