"""Independent general GPT chat. Never reads product/video/tasks nor invokes Studio tools."""
from __future__ import annotations
import json, hashlib, time, uuid
from . import db,config,openlux,secrets_store,gpt_memory_v4


def schema():
    with db.tx() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS ai_conversations (id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_messages (id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id,created_at)')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_calls (id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
    gpt_memory_v4.ensure_schema()

def conversations():
    schema()
    return db.all_('SELECT id,title,created_at,updated_at FROM ai_conversations ORDER BY updated_at DESC LIMIT 200')

def new_conversation():
    schema();t=time.time();cid='aic_'+uuid.uuid4().hex[:20]
    db.execute('INSERT INTO ai_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)',(cid,'新对话',t,t))
    return {'id':cid,'title':'新对话'}

def messages(cid):
    schema()
    if not db.one('SELECT id FROM ai_conversations WHERE id=?',(cid,)):raise ValueError('会话不存在')
    return db.all_('SELECT id,role,content,created_at FROM ai_messages WHERE conversation_id=? ORDER BY created_at,id LIMIT 1000',(cid,))

def diagnostics(model):
    from urllib import request,error
    key=secrets_store.get('openlux_api_key') or config.OPENLUX_API_KEY
    if not key:return {'status':'no_key','message':'未配置 OpenLux 密钥','model_listed':None,'paid_calls':0}
    t=time.monotonic()
    try:
        req=request.Request(openlux._url('/v1/models'),headers={'Authorization':'Bearer '+key,'Accept':'application/json'},method='GET')
        with request.urlopen(req,timeout=12) as r: raw=r.read(300000)
        obj=json.loads(raw);data=obj.get('data',[]) if isinstance(obj,dict) else []
        names=[str(x.get('id')) for x in data if isinstance(x,dict) and x.get('id')]
        return {'status':'reachable','model_listed':model in names if names else None,'count':len(names),'seconds':round(time.monotonic()-t,2),'paid_calls':0}
    except error.HTTPError as e:
        return {'status':'http_error','http_status':e.code,'message':'HTTP '+str(e.code),'paid_calls':0}
    except Exception as e:
        return {'status':'connection_error','message':type(e).__name__,'seconds':round(time.monotonic()-t,2),'paid_calls':0}

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
