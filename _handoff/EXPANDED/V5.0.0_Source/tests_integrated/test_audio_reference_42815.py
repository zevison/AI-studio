"""No-paid-call regression tests for conditional Seedance audio reference."""
from pathlib import Path
from core import audio_output, openlux, reference_logic

ROOT=Path(__file__).resolve().parents[1]


def _audio(id='a1',duration=4):
    return {'id':id,'kind':'audio','duration':duration,'path':'/tmp/voice.wav'}


def test_default_timbre_is_model_generated_not_overlay():
    policy=audio_output.select_reference_policy('人物说出新台词',[_audio()])
    assert policy['reference_option']=='timbre'
    assert policy['mode']=='model'
    assert policy['source_ids']==[]
    assert policy['reference_asset_ids']==['a1']
    prompt=audio_output.reference_instructions('timbre',[_audio()])
    assert '@audio1' in prompt and '原始台词' in prompt and '模型' in prompt


def test_content_uses_original_audio_and_enforces_duration():
    policy=audio_output.select_reference_policy('新场景',[_audio(duration=7)],'content')
    assert policy['mode']=='uploaded' and policy['source_ids']==['a1']
    policy['source_rows']=[_audio(duration=7)]
    try:
        audio_output.validate_content_duration(policy,6)
    except ValueError as e:
        assert '截断' in str(e)
    else:
        assert False, 'must protect source speech against preview truncation'
    audio_output.validate_content_duration(policy,7)


def test_no_audio_ignores_stale_hidden_selection():
    a=audio_output.select_reference_policy('Hello!',[],requested='bad-stale-value')
    b=audio_output.select_policy('Hello!',[])
    assert a['mode']==b['mode']=='model'
    assert a['source_ids']==b['source_ids']==[]
    assert a['reference_option'] is None
    assert audio_output.reference_option('content',[]) is None


def test_unknown_selection_never_silently_falls_back():
    try:
        audio_output.select_reference_policy('new',[_audio()],requested='rhythm')
    except ValueError as exc:
        assert '只能选择' in str(exc)
    else:
        assert False, 'unexpected third option must not trigger billing'


def test_existing_audio_free_soundtrack_policy_preserved():
    ref={'id':'vid','kind':'video'}
    expected=audio_output.select_policy('',[ref])
    actual=audio_output.select_reference_policy('',[ref],requested='content')
    assert actual['mode']==expected['mode']=='original'
    assert actual['source_ids']==expected['source_ids']==['vid']


def test_ark_audio_role_and_positional_alias():
    items=reference_logic.alias_records([{'id':'img','kind':'image'},_audio('a1'),_audio('a2')])
    assert [x['alias'] for x in items]==['@image 1','@audio 1','@audio 2']
    assert reference_logic.provider_prompt('旁白参考 @audio 2',items)=='旁白参考 @audio2'
    spec=openlux._content_media('audio','https://example.org/audio.mp3','reference_audio')
    assert spec['type']=='audio_url' and spec['role']=='reference_audio'


def test_both_ui_modes_present_twice_and_conditional():
    html=(ROOT/'static/index.html').read_text()
    js=(ROOT/'static/app.js').read_text()
    assert html.count('参考音色（仅使用上传音频的音色）')==2
    assert html.count('使用上传音频的内容，保持上传音频的音色')==2
    assert 'id="videoAudioUse"' in html and 'id="replicaAudioUse"' in html
    assert 'videoUse.hidden=!videoHasAudio' in js
    assert 'replicaUse.hidden=!replicaHasAudio' in js
    assert 'audio_reference_mode:selectedAudioReferenceMode(\'video\')' in js
    assert 'audio_reference_mode:selectedAudioReferenceMode(\'replica\')' in js
    assert '作为最终音轨，替换参考视频的原声音轨' not in (ROOT/'static/audio_reference.js').read_text()


def test_backend_replicates_audio_in_model_specs_and_freezes_reference_prompt():
    app=(ROOT/'app.py').read_text()
    assert "'audio_reference_mode':audio_ref_mode" in app
    assert "'audio_bound_prompt':edit_prompt" in app
    assert "prompt=payload.get('audio_bound_prompt') or workflow.workflow_prompt(wf)" in app
    assert "[ref,*all_refs]" in app


def test_single_install_and_single_start_entry():
    assert sorted(f.name for f in ROOT.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd']
    assert list(ROOT.glob('*.vbs'))==[]
