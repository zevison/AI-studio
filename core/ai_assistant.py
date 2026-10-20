"""Independent general GPT chat. Never reads product/video/tasks nor invokes Studio tools."""
from __future__ import annotations
from contextlib import closing
import json, hashlib, time, uuid
from . import db,config,openlux,secrets_store,gpt_memory_v4
from .network_trust import tls_context,network_error_message


def schema():
    with db.tx() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS ai_conversations (id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_messages (id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id,created_at)')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_calls (id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
        columns={r[1] for r in c.execute('PRAGMA table_info(ai_messages)')}
        if 'payload_json' not in columns:c.execute("ALTER TABLE ai_messages ADD COLUMN payload_json TEXT NOT NULL DEFAULT '[]'")
        columns={r[1] for r in c.execute('PRAGMA table_info(ai_calls)')}
        if 'request_id' not in columns:c.execute('ALTER TABLE ai_calls ADD COLUMN request_id TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_calls_request ON ai_calls(request_id) WHERE request_id IS NOT NULL')
    gpt_memory_v4.ensure_schema()

def conversations():
    schema()
    return db.all_('SELECT id,title,created_at,updated_at FROM ai_conversations ORDER BY updated_at DESC LIMIT 200')

def new_conversation():
    schema();t=time.time();cid='aic_'+uuid.uuid4().hex[:20]
    db.execute('INSERT INTO ai_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)',(cid,'新对话',t,t))
    return {'id':cid,'title':'新对话'}

def messages(cid,before_id=None):
    schema()
    if not db.one('SELECT id FROM ai_conversations WHERE id=?',(cid,)):raise ValueError('会话不存在')
    # Read payloads one at a time; opening history must not load every image into RAM.
    rows=[]
    cutoff=None
    if before_id:
        cutoff=db.one('SELECT created_at,id FROM ai_messages WHERE id=? AND conversation_id=?',(str(before_id),cid))
        if not cutoff:raise ValueError('历史分页位置不存在，请刷新会话')
    clause=' AND (created_at,id)<(?,?)' if cutoff else ''
    params=(cid,cutoff['created_at'],cutoff['id']) if cutoff else (cid,)
    with closing(db.connect()) as connection:
        for record in connection.execute('SELECT id,role,content,created_at,payload_json FROM (SELECT * FROM ai_messages WHERE conversation_id=?'+clause+' ORDER BY created_at DESC,id DESC LIMIT 200) ORDER BY created_at,id',params):
            row=dict(record)
            try:attachments=json.loads(row.pop('payload_json') or '[]')
            except (ValueError,TypeError):attachments=[]
            row['attachments']=[{'kind':f.get('kind'),'name':f.get('name'),
                'local_url':'/api/assistant/attachment?message_id='+row['id']+'&index='+str(i)} for i,f in enumerate(attachments)]
            rows.append(row)
    return rows


MAX_CHAT_ATTACHMENTS = 20


def attachment(message_id,index):
    schema()
    row=db.one('SELECT payload_json FROM ai_messages WHERE id=?',(str(message_id),))
    if not row:raise ValueError('附件记录不存在')
    try:
        number=int(index)
        if not 0<=number<MAX_CHAT_ATTACHMENTS:raise ValueError()
        return json.loads(row['payload_json'])[number]
    except (ValueError,TypeError,IndexError):raise ValueError('附件编号无效') from None


def call_state(cid):
    schema()
    return db.one('SELECT id,status,error,created_at,updated_at FROM ai_calls WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1',(str(cid),))


def acknowledge_unknown(payload):
    """Release a conversation, never retry or assert that an unknown charge is zero."""
    schema()
    if payload.get('acknowledge_possible_charge') is not True:raise ValueError('请确认旧请求可能已扣费；此操作不会重发旧请求')
    with db.tx() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM ai_calls WHERE id=? AND conversation_id=?',(str(payload.get('call_id') or ''),str(payload.get('conversation_id') or ''))).fetchone()
        if not row:raise ValueError('请求记录不存在')
        if row['status']=='submission_unknown' and time.time()-row['updated_at']<240:
            raise ValueError('原请求可能仍在运行，请等待至少4分钟并刷新记录')
        if row['status'] not in ('submission_unknown','partial','interrupted_acknowledged'):raise ValueError('该请求无需解除等待')
        c.execute("UPDATE ai_calls SET status='interrupted_acknowledged',updated_at=? WHERE id=?",(time.time(),row['id']))
    return {'preserved':True,'resubmitted':False,'possible_charge':True}

def diagnostics(model):
    from urllib import request,error
    key=secrets_store.get('openlux_api_key') or config.OPENLUX_API_KEY
    if not key:return {'status':'no_key','message':'未配置 OpenLux 密钥','model_listed':None,'paid_calls':0}
    t=time.monotonic()
    try:
        req=request.Request(openlux._url('/v1/models'),headers={'Authorization':'Bearer '+key,'Accept':'application/json'},method='GET')
        with request.urlopen(req,timeout=12,context=tls_context()) as r: raw=r.read(300000)
        obj=json.loads(raw);data=obj.get('data',[]) if isinstance(obj,dict) else []
        names=[str(x.get('id')) for x in data if isinstance(x,dict) and x.get('id')]
        return {'status':'reachable','model_listed':model in names if names else None,'count':len(names),'seconds':round(time.monotonic()-t,2),'paid_calls':0}
    except error.HTTPError as e:
        return {'status':'http_error','http_status':e.code,'message':'HTTP '+str(e.code),'paid_calls':0}
    except Exception as e:
        return {'status':'connection_error','message':network_error_message(e),'seconds':round(time.monotonic()-t,2),'paid_calls':0}

def send(payload):
    schema()
    cid=str(payload.get('conversation_id') or '')
    text=str(payload.get('message') or '').strip()
    model=str(payload.get('model') or 'gpt-6.1-sol').strip()
    if not text or len(text)>12000:raise ValueError('消息长度必须在1—12000字符')
    if not model or len(model)>120 or not all(x.isalnum() or x in '-._:/@+' for x in model):raise ValueError('模型编号格式无效')
    conv=db.one('SELECT * FROM ai_conversations WHERE id=?',(cid,))
    if not conv:raise ValueError('会话不存在')
    if payload.get('approve_model_cost') is not True:raise ValueError('发送付费模型消息前必须确认')
    history=messages(cid)
    if history and history[-1]['role']=='user':raise ValueError('上一条请求结果未确定，禁止无条件重复提交付费请求；请先核账或新建会话')
    now=time.time();uid='msg_'+uuid.uuid4().hex[:20];call_id='call_'+uuid.uuid4().hex[:20]
    with db.tx() as c:
        c.execute('INSERT INTO ai_messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',(uid,cid,'user',text,now))
        c.execute('INSERT INTO ai_calls(id,conversation_id,model,status,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(call_id,cid,model,'submission_unknown','',now,now))
        if conv['title']=='新对话':c.execute('UPDATE ai_conversations SET title=?,updated_at=? WHERE id=?',(text[:36],now,cid))
        else:c.execute('UPDATE ai_conversations SET updated_at=? WHERE id=?',(now,cid))
    system=('你是一个通用 GPT 聊天助手，不是商品助手，不会读取 Seedance Studio 商品库或视频任务。'
            '请直接回答用户的问题，可以协助知识解释、翻译、写作和一般建议。'
            '所有英文术语附中文解释。不得声称已操作工作台。'
            +gpt_memory_v4.context_text())
    chain=[{'role':'system','content':system}]+[{'role':x['role'],'content':x['content']} for x in history[-14:]]
    chain.append({'role':'user','content':text})
    try:
        response=openlux.request_json('POST','/v1/chat/completions',{'model':model,'messages':chain},timeout=65,retries=0)
        first=(response.get('choices') or [{}])[0].get('message') or {}
        answer=first.get('content')
        if isinstance(answer,list):answer='\n'.join(str(x.get('text','')) for x in answer if isinstance(x,dict))
        if not isinstance(answer,str) or not answer.strip():raise ValueError('上游没有返回可显示的文字，结果不明')
        stamp=time.time()
        with db.tx() as c:
            c.execute('INSERT INTO ai_messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',('msg_'+uuid.uuid4().hex[:20],cid,'assistant',answer,stamp))
            c.execute('UPDATE ai_calls SET status=?,updated_at=? WHERE id=?',('completed',stamp,call_id))
        return {'answer':answer,'model':response.get('model') or model,'usage':response.get('usage') or {},'call_id':call_id}
    except Exception as e:
        db.execute('UPDATE ai_calls SET error=?,updated_at=? WHERE id=?',(str(e)[:350],time.time(),call_id))
        raise ValueError('GPT 请求未取得完整回复（'+call_id+'）：'+str(e)[:180]+'。不能自动重试，以免重复扣费') from None
