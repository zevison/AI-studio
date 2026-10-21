"""Chat persistence, duplicate admission and interrupted streams; no network calls."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import time
import urllib.error
from unittest.mock import patch

import pytest
from core import ai_assistant as store, db, gpt_pro_v41 as chat


@pytest.fixture
def conversation(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'chat.db')
    db.init_db()
    monkeypatch.setattr(chat,'_key',lambda:'test-only')
    monkeypatch.setattr(chat.gpt_memory_v4,'context_text',lambda:'')
    return store.new_conversation()['id']


def message(cid,**extra):
    return {'conversation_id':cid,'message':'分析资料','approve_model_cost':True,**extra}


class Stream(io.BytesIO):
    headers={'Content-Type':'text/event-stream'}


def stream(text='成功'):
    value={'choices':[{'delta':{'content':text}}]}
    return Stream(('data: '+json.dumps(value)+'\n\ndata: [DONE]\n\n').encode())


def test_completed_reply_survives_closed_browser(conversation):
    def disconnected(*args):raise BrokenPipeError()
    with patch.object(chat.provider_transport,'urlopen',return_value=stream()):
        chat.chat_events(message(conversation),disconnected)
    assert store.messages(conversation)[-1]['content']=='成功'
    assert store.call_state(conversation)['status']=='completed'


def test_document_persists_and_returns_in_next_turn(conversation):
    attachment={'name':'facts.txt','data':base64.b64encode('商品参数 160 PSI'.encode()).decode()}
    with patch.object(chat.provider_transport,'urlopen',return_value=stream()):
        chat.chat_events(message(conversation,attachments=[attachment]),lambda *args:None)
    saved=store.messages(conversation)
    assert 'value' not in saved[0]['attachments'][0]
    assert store.attachment(saved[0]['id'],0)['value']=='商品参数 160 PSI'
    _,_,_,_,_,chain=chat._start(message(conversation,message='继续'))
    assert any('160 PSI' in str(item['content']) for item in chain[:-1])


def image_attachments(count):
    from PIL import Image
    buffer=io.BytesIO()
    Image.new('RGB',(8,8),'blue').save(buffer,format='PNG')
    data=base64.b64encode(buffer.getvalue()).decode()
    return [{'name':f'picture-{i+1}.png','data':data} for i in range(count)]


def test_twenty_images_are_sent_saved_and_reopenable(conversation):
    attachments=image_attachments(20)
    with patch.object(chat.provider_transport,'urlopen',return_value=stream()):
        chat.chat_events(message(conversation,attachments=attachments),lambda *args:None)
    saved=store.messages(conversation)
    assert len(saved[0]['attachments'])==20
    for index in range(20):
        assert store.attachment(saved[0]['id'],index)['name']==f'picture-{index+1}.png'
    _,_,_,_,_,chain=chat._start(message(conversation,message='继续比较图片'))
    assert sum(p.get('type')=='image_url' for m in chain if isinstance(m['content'],list) for p in m['content'])==20
    for index in (-1,20):
        with pytest.raises(ValueError,match='附件编号无效'):store.attachment(saved[0]['id'],index)


def test_twenty_one_attachments_rejected_before_credentials_or_persistence(conversation,monkeypatch):
    def forbidden():raise AssertionError('Must not read credentials or call a provider')
    monkeypatch.setattr(chat,'_key',forbidden)
    with pytest.raises(ValueError,match='最多上传20'):
        chat._start(message(conversation,attachments=image_attachments(21)))
    assert store.messages(conversation)==[]


def test_total_size_guard_still_applies_to_many_attachments():
    raw=[{'name':f'{i}.txt','data':base64.b64encode(b'x'*(1024*1024)).decode()} for i in range(11)]
    with pytest.raises(ValueError,match='总大小超过10MB'):chat._files(raw)


def test_frontend_backend_attachment_limits_match():
    from pathlib import Path
    import re
    script=(Path(__file__).parents[1]/'static/assistant_bright.js').read_text(encoding='utf8')
    assert int(re.search(r'const MAX_CHAT_ATTACHMENTS=(\d+)',script)[1])==store.MAX_CHAT_ATTACHMENTS


def test_duplicate_request_and_parallel_same_conversation_reserve_once(conversation):
    payload=message(conversation,request_id='repeat_request_123456789')
    def admit(_):
        try:return chat._start(payload)[0]
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:accepted=list(pool.map(admit,range(4)))
    assert sum(x is not None for x in accepted)==1
    assert len(store.messages(conversation))==1


def test_definite_rejection_does_not_lock_conversation(conversation):
    events=[]
    error=urllib.error.HTTPError('https://example.invalid',400,'Bad request',{},io.BytesIO())
    with patch.object(chat.provider_transport,'urlopen',side_effect=error):
        chat.chat_events(message(conversation),lambda name,value:events.append((name,value)))
    assert store.call_state(conversation)['status']=='rejected'
    assert events[-1][1]['state']=='rejected'
    assert chat._start(message(conversation,message='修正后新问题'))[0]


def test_unknown_request_requires_acknowledgement_without_replay(conversation):
    call_id=chat._start(message(conversation))[0]
    payload={'conversation_id':conversation,'call_id':call_id,'acknowledge_possible_charge':True}
    with pytest.raises(ValueError,match='4分钟'):store.acknowledge_unknown(payload)
    db.execute('UPDATE ai_calls SET updated_at=? WHERE id=?',(time.time()-300,call_id))
    with patch.object(chat.provider_transport,'urlopen',side_effect=AssertionError('must not submit')):
        result=store.acknowledge_unknown(payload)
    assert result['resubmitted'] is False and result['possible_charge'] is True
    assert chat._start(message(conversation,message='另一个问题'))[0]


def test_partial_stream_is_saved_before_timeout(conversation):
    class Interrupted(Stream):
        def __iter__(self):
            yield ('data: '+json.dumps({'choices':[{'delta':{'content':'已生成的半句'}}]})+'\n').encode()
            raise TimeoutError()
    with patch.object(chat.provider_transport,'urlopen',return_value=Interrupted()):
        chat.chat_events(message(conversation),lambda *args:None)
    assert store.messages(conversation)[-1]['content']=='已生成的半句'
    assert store.call_state(conversation)['status']=='partial'


def test_fake_image_and_zip_bomb_metadata_refused():
    with pytest.raises(ValueError,match='图片无效'):
        chat._files([{'name':'fake.png','data':base64.b64encode(b'not an image').decode()}])
    import zipfile
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr('word/document.xml',b'X'*(41*1024*1024))
    with patch.object(zipfile.ZipFile,'open',side_effect=AssertionError('must not decompress oversized document')):
        with pytest.raises(ValueError,match='解压内容过大'):chat._check_document_zip(archive.getvalue())


def test_paging_does_not_drop_equal_timestamp_messages(conversation):
    with db.tx() as connection:
        for index in range(205):
            connection.execute('INSERT INTO ai_messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',
                (f'msg_{index:04d}',conversation,'user',str(index),1000))
    newest=store.messages(conversation)
    older=store.messages(conversation,newest[0]['id'])
    assert len(newest)==200 and len(older)==5
    assert len({r['id'] for r in [*older,*newest]})==205


def test_delete_chat_preserves_charge_audit_and_blocks_active_call(conversation):
    call_id=chat._start(message(conversation))[0]
    payload={'conversation_id':conversation,'confirmed':True}
    with pytest.raises(ValueError,match='不确定'):chat.gpt_memory_v4.delete_conversation(payload)
    db.execute("UPDATE ai_calls SET status='completed' WHERE id=?",(call_id,))
    result=chat.gpt_memory_v4.delete_conversation(payload)
    assert result['deleted']
    assert db.one('SELECT id FROM ai_calls WHERE id=?',(call_id,))
    assert not db.one('SELECT id FROM ai_messages WHERE conversation_id=?',(conversation,))
