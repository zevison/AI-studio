"""No-paid-call checks for explicit @audio-to-speaker routing and installer."""
from pathlib import Path
import pytest
from core import voice_bindings, audio_output, reference_logic

ROOT=Path(__file__).resolve().parents[1]

def files(n=1):
    return [{'id':'image-1','kind':'image'}, *[{'id':f'audio-{j}','kind':'audio','duration':7} for j in range(1,n+1)]]

@pytest.mark.parametrize('prompt,target',[
    ('两个人对话，@image 1 中右边的男生使用 @audio 1 的音色','@image 1 中 右边的男生'),
    ('左边女生用 @audio 1','左边女生'),
    ('男生的声音参考 @audio 1','男生'),
    ('@audio 1 用于右边的男生','右边的男生'),
    ('角色 1 使用 @audio 1','角色 1'),
    ('人物 1 使用 @audio1','人物 1'),
    ('two people talking, the man on the right uses @audio 1','the man on the right'),
    ('@audio 1 for the girl on the left','the girl on the left'),
    ('the boy uses @audio 1','the boy'),
])
def test_explicit_audio_speaker_assignment(prompt,target):
    result=voice_bindings.plan(prompt, files(), 'timbre')
    assert len(result['bindings'])==1
    assert result['bindings'][0]['target']==target
    assert result['bindings'][0]['source']=='explicit_at'
    assert '@audio1' in result['instruction']
    assert target in result['instruction']

@pytest.mark.parametrize('prompt',[
    '两个人对话，参考 @audio 1 的音色',
    '两个女孩聊天，一个用 @audio 1',
    'two people talking, use @audio 1',
    '男人和女孩对话，保持上传音频的声音',
])
def test_multi_people_without_explicit_mapping_fails_before_preview(prompt):
    with pytest.raises(ValueError,match='不会猜测'):
        voice_bindings.plan(prompt,files(),'timbre')


def test_two_characters_two_clips_both_bound():
    result=voice_bindings.plan('两个角色对话，@image 1 中右边的男生使用 @audio 1；左边女生使用 @audio 2',files(2),'content')
    assert len(result['bindings'])==2
    assert result['bindings'][0]['alias']=='@audio 1'
    assert result['bindings'][1]['alias']=='@audio 2'
    assert '原台词' in result['instruction']
    assert any('多名说话人' in w for w in result['warnings'])


def test_unassigned_second_audio_blocks_for_multi_audio():
    with pytest.raises(ValueError,match='@audio 2'):
        voice_bindings.plan('左边女生使用 @audio 1',files(2),'timbre')


def test_same_audio_conflicting_roles_blocks():
    with pytest.raises(ValueError,match='同时绑定'):
        voice_bindings.plan('右边的男生使用 @audio 1；左边女生使用 @audio 1',files(),'timbre')


def test_two_audio_to_same_character_blocks():
    with pytest.raises(ValueError,match='唯一音色'):
        voice_bindings.plan('男生使用 @audio 1；男生使用 @audio 2',files(2),'timbre')


def test_misspelled_alias_blocks_even_single_character():
    with pytest.raises(ValueError,match='不存在'):
        voice_bindings.plan('男生使用 @audio 3',files(),'timbre')


def test_implicit_only_for_single_speaker_unambiguous():
    result=voice_bindings.plan('一个女生说商品介绍',files(),'timbre')
    assert result['bindings'][0]['source']=='single_voice_default'
    assert '未指定人物' in result['warnings'][0]


def test_no_audio_never_changes_original_workflow():
    assert voice_bindings.plan('两人对话',[{'id':'im','kind':'image'}],None)['bindings']==[]
    assert audio_output.select_reference_policy('正常生成',[],requested='stale-bad')['reference_option'] is None


def test_only_two_uploaded_audio_options():
    assert audio_output.AUDIO_REFERENCE_OPTIONS=={'timbre','content'}
    assert audio_output.select_reference_policy('say hi',files(),'timbre')['mode']=='model'
    assert audio_output.select_reference_policy('say hi',files(),'content')['mode']=='uploaded'


def test_payload_and_preview_path_frozen_without_paid_submission():
    app=(ROOT/'app.py').read_text('utf-8')
    assert app.count("'audio_voice_bindings':voice_plan['bindings']")>=4
    assert 'voice_bindings.plan(prompt,refs,audio_ref_mode)' in app
    assert "voice_bindings.plan(row.get('instruction')" in app
    assert "except ValueError as e:return self.err(str(e),409)" in app
    html=(ROOT/'static/index.html').read_text('utf-8')
    js=(ROOT/'static/app.js').read_text('utf-8')
    assert html.count('audio-role-tip')==2
    assert 'audio_voice_bindings' in js


def test_new_pc_install_single_files_progress_labels_and_no_keys():
    assert {x.name for x in ROOT.glob('*.cmd')}=={'00_START.cmd','01_INSTALL.cmd'}
    assert not list(ROOT.glob('*.vbs'))
    installer=(ROOT/'tools/install_new_pc.ps1').read_text('utf-8-sig')
    assert 'Draw-Phase 5' in installer
    assert 'Draw-Download $received $total' in installer
    assert 'Download-OfficialPython $officialUrl $temp' in installer
    assert 'Validate-PythonInstaller $temp' in installer
    assert 'python.org' in installer
    bootstrap=(ROOT/'tools/bootstrap_install.py').read_text('utf-8')
    assert 'show_install_stage(7' in bootstrap
    assert 'install_complete_analysis()' in bootstrap
    assert (ROOT/'tools/launch_silent.vbs').is_file()
    assert (ROOT/'app.py').is_file()


def test_new_version_in_all_primary_sources():
    for relative in ('core/config.py','static/index.html','tools/bootstrap_install.py'):
        assert '5.2.0' in (ROOT/relative).read_text('utf-8')
