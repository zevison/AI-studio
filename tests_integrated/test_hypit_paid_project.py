from pathlib import Path
import json
import pytest
from core.hypit_paid_project import exact_text_request

SOURCE='''<?svml using="@hypit/markup@1"?><svml>
<import as="copy" from="@hypit/text@1"/><import as="video" from="@hypit/seedance@1"/>
<copy:Value id="direction">A ceramic cup on a table.</copy:Value>
<video:TextVideo id="final" model="2.5" prompt={direction} duration="4"
aspect-ratio="9:16" resolution="720p" generate-audio="false" web-search="false"/></svml>'''


def project(tmp_path,source=SOURCE):
    (tmp_path/'package.json').write_text(json.dumps({'hypit':{'project':True}}))
    (tmp_path/'main.svml').write_text(source,encoding='utf8')
    (tmp_path/'build.svrun').write_text('<?svml using="@hypit/markup/run@1"?><svrun version="1"><author source="./main.svml"/><target output="final.video"/></svrun>')
    return tmp_path


def test_quote_extracts_real_literal_settings_with_arbitrary_author_prefixes(tmp_path):
    request,size=exact_text_request(project(tmp_path))
    assert size==(720,1280)
    assert request['content']==[{'type':'text','text':'A ceramic cup on a table.'}]
    assert request['duration']==4 and request['generate_audio'] is False


@pytest.mark.parametrize('old,new',[
    ('model="2.5"','model="mini"'),('web-search="false"','web-search="true"'),
    ('prompt={direction}','prompt={missing}'),('duration="4"','duration="-1"'),
    ('TextVideo','ReferenceVideo'),
    ('<video:TextVideo','<video:TextVideo id="second" model="2.5"/><video:TextVideo'),
])
def test_unquoted_or_changed_generation_semantics_rejected(tmp_path,old,new):
    with pytest.raises((PermissionError,ValueError)):
        exact_text_request(project(tmp_path,SOURCE.replace(old,new)))
