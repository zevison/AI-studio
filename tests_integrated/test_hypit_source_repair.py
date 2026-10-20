from pathlib import Path
import json
import pytest
from core.hypit_cli_guard import validate_project_boundary
from core.hypit_source_repair import repair_vocabulary_imports, repair_timeline_origin


def project(tmp_path,source):
    (tmp_path/'package.json').write_text(json.dumps({'hypit':{'project':True}}))
    (tmp_path/'main.svml').write_text(source,encoding='utf8')
    return tmp_path


def test_named_time_boundaries_are_not_file_paths(tmp_path):
    root=project(tmp_path,'<svml><import as="time" from="@hypit/timeline-author@1"/>'
        '<time:Timeline id="p" clock={clock} end="shot.end"><time:Window id="shot" from="start" for="1s"/></time:Timeline></svml>')
    validate_project_boundary(root)


@pytest.mark.parametrize('source',[
    '<svml><import as="x" from="https://evil.invalid/a"/></svml>',
    '<svml><import as="x" from="@hypit/not-approved@1"/></svml>',
    '<!DOCTYPE svml [<!ENTITY f SYSTEM "file:///secrets">]><svml>&f;</svml>',
    '<svml><media:Video src="https://evil.invalid/x.mp4"/></svml>',
    '<svml><x value={process.env.SECRET + 1}/></svml>',
    '<svml><import as="x" from="__hypit_reference__"/></svml>',
])
def test_external_sources_and_executable_references_fail_closed(tmp_path,source):
    with pytest.raises(PermissionError):validate_project_boundary(project(tmp_path,source))


def test_repair_only_known_import_syntax_and_preserves_original(tmp_path):
    text='<svml><import as="media" source="@hypit/media@1"/><import as="style" source="./look.svs"/></svml>'
    root=project(tmp_path,text);(root/'look.svs').write_text('<sheet version="1">film.main { background: #000000; }</sheet>')
    diagnostic={'error':{'message':'@hypit/media@1 must be one exact npm package name'}}
    result=repair_vocabulary_imports(root,diagnostic)
    assert result['paid_calls']==0 and result['modules']==['@hypit/media@1']
    repaired=(root/'main.svml').read_text(encoding='utf8')
    assert 'from="@hypit/media@1"' in repaired and 'source="./look.svs"' in repaired
    assert (root/result['backup']).read_text(encoding='utf8')==text


def test_unknown_error_never_triggers_a_rewrite(tmp_path):
    root=project(tmp_path,'<svml><import as="media" source="@hypit/media@1"/></svml>')
    before=(root/'main.svml').read_bytes()
    assert repair_vocabulary_imports(root,{'error':{'message':'Unknown output'}}) is None
    assert (root/'main.svml').read_bytes()==before


def test_timeline_origin_repair_requires_real_diagnostic_and_preserves_source(tmp_path):
    text = ('<svml><import as="t" from="@hypit/timeline-author@1"/>'
            '<t:Timeline id="p" clock={clock} end="shot.end">'
            '<t:Window id="shot" from="0" for={prepared.extent}/></t:Timeline></svml>')
    root = project(tmp_path, text)
    assert repair_timeline_origin(root, {'error': {'message': 'Unknown output'}}) is None
    result = repair_timeline_origin(root, {'error': {'message': 'Timeline expression 0 is not an Instant.'}})
    assert result['paid_calls'] == 0
    assert (root/result['backup']).read_text('utf8') == text
    assert (root/'main.svml').read_text('utf8') == text.replace('from="0"', 'from="start"')
    assert repair_timeline_origin(root, {'error': {'message': 'Timeline expression 0 is not an Instant.'}}) is None


@pytest.mark.parametrize('body', [
    '<!-- <t:Window from="0"/> -->',
    '<![CDATA[<t:Window from="0"/>]]>',
    '<t:Window id="s" from="1" for="1s"/>',
    '<t:Window id="s" from="0s" for="1s"/>',
    '<other:Window id="s" from="0" for="1s"/>',
    '<t:Window id="s" from="start" label=\'from="0"\' for="1s"/>',
])
def test_timeline_repair_does_not_change_other_semantics(tmp_path, body):
    text = '<svml><import as="t" from="@hypit/timeline-author@1"/>' + body + '</svml>'
    root = project(tmp_path, text)
    assert repair_timeline_origin(root, {'error': {'message': 'Timeline expression 0 is not an Instant.'}}) is None
    assert (root/'main.svml').read_text('utf8') == text
