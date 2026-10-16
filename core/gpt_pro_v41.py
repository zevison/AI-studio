"""High-capability, isolated GPT conversation for Seedance Studio.

One paid request per user action; streamed text; optional provider-hosted web_search.
No product/video/ads/TikTok tool calls or credentials in logs.
"""
from __future__ import annotations
from . import history_center_v423  # Seedance V4.2.3 Unified History
import base64, io, json, mimetypes, re, time, uuid, urllib.request, urllib.error, socket, hashlib, zipfile
from xml.etree import ElementTree
from . import ai_assistant, config, db, gpt_memory_v4, openlux, secrets_store

PRESETS = [
    {'id':'gpt-6-astra','label':'GPT-6 Astra｜高性能·复杂任务','tier':'strong','description':'强推理、研究和编程，可能较慢'},
    {'id':'gpt-5.6-sol','label':'GPT-5.6 Sol｜均衡能力与速度','tier':'balanced','description':'日常讨论、写作和多数分析'},
    {'id':'gpt-5.6-sol-high','label':'GPT-5.6 Sol High｜高强度思考','tier':'strong','description':'复杂分析（需验证后缀权限）'},
    {'id':'gpt-6.1-sol','label':'GPT-6.1 Sol｜历史模型','tier':'legacy','description':'原有对话模型（以账户权限为准）'},
]
_CACHE={'at':0.,'items':[],'error':None}


def _key():
    return (secrets_store.get('openlux_api_key') or config.OPENLUX_API_KEY or '').strip()


def models(force=False):
    now=time.monotonic()
    if not force and _CACHE['items'] and now-_CACHE['at']<300:
        return {'models':_CACHE['items'],'presets':PRESETS,'verified':True,'error':None}
    if not _key():return {'models':[],'presets':PRESETS,'verified':False,'error':'尚未配置 OpenLux 密钥'}
    try:
        req=urllib.request.Request(openlux._url('/v1/models'),headers={'Authorization':'Bearer '+_key(),'Accept':'application/json'},method='GET')
        with urllib.request.urlopen(req,timeout=12) as r: data=json.loads(r.read(750000))
        items=data.get('data',[]) if isinstance(data,dict) else []
        ids=sorted({str(x.get('id')) for x in items if isinstance(x,dict) and x.get('id')})
        # Chat model names only; models endpoint can also return image/video/embedding models.
        allow=('gpt-','claude-','gemini-','deepseek-','qwen-','glm-','grok-','o3','o4')
        selected=[m for m in ids if m.lower().startswith(allow) and not any(x in m.lower() for x in ('image','embedding','tts','transcribe','audio','video'))]
        _CACHE.update(at=now,items=selected,error=None)
        return {'models':selected,'presets':PRESETS,'verified':True,'error':None}
    except urllib.error.HTTPError as ex:
        code=ex.code
        return {'models':[],'presets':PRESETS,'verified':False,'error':'OpenLux 模型列表请求失败（HTTP '+str(code)+'），尚无法验证模型权限'}
    except Exception as ex:
        return {'models':[],'presets':PRESETS,'verified':False,'error':'模型列表网络异常：'+type(ex).__name__}


def _files(raw):
    if not isinstance(raw,list) or len(raw)>3:raise ValueError('每次最多上传3个附件')
    results=[]
    total=0
    for x in raw:
        if not isinstance(x,dict):raise ValueError('附件结构不正确')
        name=str(x.get('name') or 'attachment')[:130]
        text=str(x.get('data') or '')
        if len(text)>12_000_000:raise ValueError('附件内容过大，单次最多约8MB编码数据')
        try: buf=base64.b64decode(text,validate=True)
        except Exception:raise ValueError('附件编码无效') from None
        total+=len(buf)
        if total>10*1024*1024:raise ValueError('附件总大小超过10MB')
        ext=name.lower().rsplit('.',1)[-1] if '.' in name else ''
        if ext in ('png','jpg','jpeg','webp'):
            if len(buf)>4*1024*1024:raise ValueError('单张图片不得超过4MB')
            mime={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp'}[ext]
            results.append({'kind':'image','name':name,'value':'data:'+mime+';base64,'+text})
        elif ext in ('txt','md','csv','json'):
            content=buf.decode('utf-8-sig','replace')[:30000]
            results.append({'kind':'text','name':name,'value':content})
        elif ext=='pdf':
            try:
                from pypdf import PdfReader
                pages=PdfReader(io.BytesIO(buf)).pages[:35]
                content='\n'.join((p.extract_text() or '') for p in pages)[:30000]
            except ImportError:raise ValueError('PDF 文档解析需要 pypdf，请检查 requirements.txt') from None
            except Exception:raise ValueError('PDF 解析失败或加密，不会猜测内容') from None
            if not content.strip():raise ValueError('这份 PDF 没有可提取的文字，扫描件需先进行 OCR 识别')
            results.append({'kind':'text','name':name,'value':content})
        elif ext=='docx':
            try:
                from docx import Document
                content='\n'.join(p.text for p in Document(io.BytesIO(buf)).paragraphs)[:30000]
            except Exception:raise ValueError('DOCX 文档读取失败') from None
            results.append({'kind':'text','name':name,'value':content})
        elif ext=='xlsx':
            try:
                from openpyxl import load_workbook
                wb=load_workbook(io.BytesIO(buf),read_only=True,data_only=True)
                pieces=[]
                for ws in wb.worksheets[:3]:
                    pieces.append('工作表：'+ws.title)
                    for row in ws.iter_rows(min_row=1,max_row=150,max_col=15,values_only=True):
                        pieces.append('\t'.join(str(v)[:200] if v is not None else '' for v in row))
                wb.close();content='\n'.join(pieces)[:30000]
            except Exception:raise ValueError('XLSX 工作簿读取失败') from None
            results.append({'kind':'text','name':name,'value':content})
        else:raise ValueError('仅支持图片 PNG/JPG/WEBP 与 TXT/MD/CSV/JSON/PDF/DOCX/XLSX 文档')
    return results


def _prior_history(cid):
    return ai_assistant.messages(cid)[-20:]


def _start(payload):
    ai_assistant.schema()
    cid=str(payload.get('conversation_id') or '')
    msg=str(payload.get('message') or '').strip()
    model=str(payload.get('model') or 'gpt-6-astra').strip()
    if not msg or len(msg)>12000:raise ValueError('消息必须为1—12000字符')
    if not model or len(model)>130 or not re.fullmatch(r'[A-Za-z0-9._:+@/-]+',model):raise ValueError('模型编号不正确')
    if payload.get('approve_model_cost') is not True:raise ValueError('请明确确认本次模型调用费用')
    convo=db.one('SELECT * FROM ai_conversations WHERE id=?',(cid,))
    if not convo:raise ValueError('找不到对应对话')
    hist=_prior_history(cid)
    if hist and hist[-1]['role']=='user':raise ValueError('上次付费请求结果不确定，请先核对账单再新建对话；禁止重复提交')
    effort=str(payload.get('thinking') or 'default')
    if effort not in ('default','low','medium','high','xhigh'):raise ValueError('思考强度无效')
    mode=str(payload.get('mode') or 'chat')
    if mode not in ('chat','web'):raise ValueError('模式无效')
    attachments=_files(payload.get('attachments',[]))
    if mode=='web' and attachments:raise ValueError('当前联网模式不能同时附带文件；请选择普通对话模式上传文件')
    if mode=='web' and not (model.startswith('gpt-') or model.startswith('o')):
        raise ValueError('OpenLux 网页搜索需使用兼容 Responses（响应）接口的 GPT 模型；暂不支持此模型')
    # Do not bill if a key is missing.
    if not _key():raise ValueError('尚未配置 OpenLux 密钥')
    now=time.time();cid_call='gp41_'+uuid.uuid4().hex[:20]
    with db.tx() as c:
        c.execute('INSERT INTO ai_messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',('msg_'+uuid.uuid4().hex[:20],cid,'user',msg,now))
        c.execute('INSERT INTO ai_calls(id,conversation_id,model,status,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(cid_call,cid,model,'submission_unknown','',now,now))
        c.execute('UPDATE ai_conversations SET title=?,updated_at=? WHERE id=?',(msg[:36] if convo['title']=='新对话' else convo['title'],now,cid))
    sys_text=('你是高能力的通用 GPT 助手。中文优先，英文术语附中文解释。必要时解释不确定性。'
              '你可以分析用户主动上传的文件或图片，但不可以读取 Seedance Studio 的商品库、视频任务或账户数据，'
              '也不能声称拥有未经提供的外部数据、新闻和天气。'
              '对于未验证的实时事实，不得编造。\n'+gpt_memory_v4.context_text())
    msgs=[{'role':'system','content':sys_text}]+[{'role':h['role'],'content':h['content']} for h in hist]
    if attachments:
        pieces=[{'type':'text','text':msg}]
        for f in attachments:
            if f['kind']=='image':pieces.append({'type':'image_url','image_url':{'url':f['value']}})
            else:pieces.append({'type':'text','text':'用户主动上传文档《'+f['name']+'》内容：\n'+f['value']})
        msgs.append({'role':'user','content':pieces})
    else:msgs.append({'role':'user','content':msg})
    return cid_call,cid,model,effort,mode,msgs


def _finish(call_id,cid,model,answer,usage=None,status='completed'):
    # Store actual completion time once, shared by persisted message and SSE.
    stamp=time.time()
    answer=str(answer or '')
    if len(answer)>0:
        db.execute('INSERT INTO ai_messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)',
                   ('msg_'+uuid.uuid4().hex[:20],cid,'assistant',answer,stamp))
    db.execute('UPDATE ai_calls SET status=?,updated_at=? WHERE id=?',(status,stamp,call_id))
    try:
        history_center_v423.record_gpt_usage(call_id,usage)
    except Exception:
        # Telemetry must never fail an otherwise successful paid GPT reply.
        pass
    call=db.one('SELECT created_at FROM ai_calls WHERE id=?',(call_id,))
    sent_at=float(call['created_at']) if call else stamp
    return {'call_id':call_id,'model':model,'usage':usage or {},
            'complete':status=='completed','sent_at':sent_at,'reply_at':stamp,
            'elapsed_seconds':round(max(0.,stamp-sent_at),2)}


def chat_events(payload,emit):
    """emit(name,data) gets SSE events; no automatic retries, not even after disconnect."""
    call_id=None;cid=None
    try:
        call_id,cid,model,effort,mode,msgs=_start(payload)
        call=db.one('SELECT created_at FROM ai_calls WHERE id=?',(call_id,))
        emit('begin',{'call_id':call_id,'model':model,'mode':mode,
                      'sent_at':float(call['created_at']) if call else time.time()})
        if mode=='web':
            request_body={'model':model,'input':msgs,'tools':[{'type':'web_search'}]}
            if effort!='default':request_body['reasoning']={'effort':effort}
            emit('status',{'text':'正在请求供应商联网工具（由当前通道权限决定）…'})
            resp=openlux.request_json('POST','/v1/responses',request_body,timeout=95,retries=0)
            answer=resp.get('output_text')
            if not answer:
                chunks=[]
                for part in resp.get('output',[]):
                    if not isinstance(part,dict):continue
                    for c in part.get('content',[]):
                        if isinstance(c,dict) and c.get('text'):chunks.append(str(c['text']))
                answer='\n'.join(chunks)
            if not answer:raise ValueError('联网响应缺少可显示文本；此通道可能不支持 web_search，请查看供应商权限')
            emit('delta',{'text':answer}); info=_finish(call_id,cid,resp.get('model',model),answer,resp.get('usage'))
            emit('done',info);return
        body={'model':model,'messages':msgs,'stream':True}
        if effort!='default':body['reasoning_effort']=effort
        req=urllib.request.Request(openlux._url('/v1/chat/completions'),data=json.dumps(body,ensure_ascii=False).encode('utf-8'),headers={
            'Content-Type':'application/json','Accept':'text/event-stream', 'Authorization':'Bearer '+_key(),
            'User-Agent':'SeedanceStudio/GPTV41'},method='POST')
        answer=[];usage={};is_done=False
        with urllib.request.urlopen(req,timeout=95) as resp:
            # Some OpenAI-compatible gateways ignore streaming and return one JSON result.
            ctype=str(resp.headers.get('Content-Type','')).lower()
            if 'text/event-stream' not in ctype:
                result=json.loads(resp.read(12_000_000))
                msg=(result.get('choices') or [{}])[0].get('message') or {}
                value=msg.get('content') or ''
                if not isinstance(value,str):raise ValueError('返回类型未支持，请换用兼容聊天接口的模型')
                answer.append(value);usage=result.get('usage') or {};is_done=True
                emit('delta',{'text':value})
            else:
                for raw in resp:
                    line=raw.decode('utf-8','replace').strip()
                    if not line.startswith('data:'):continue
                    data=line[5:].strip()
                    if data=='[DONE]':is_done=True;break
                    try: chunk=json.loads(data)
                    except ValueError:continue
                    if isinstance(chunk,dict) and chunk.get('error'):
                        raise ValueError('模型流式输出失败，查看 OpenLux 日志')
                    if chunk.get('usage'):usage=chunk['usage']
                    for choice in chunk.get('choices') or []:
                        delta=choice.get('delta') or {}
                        text=delta.get('content')
                        if isinstance(text,str) and text:
                            answer.append(text);emit('delta',{'text':text})
                        if choice.get('finish_reason') and choice.get('finish_reason') not in ('stop','length'):
                            raise ValueError('生成提前终止：'+str(choice['finish_reason']))
        result=''.join(answer)
        if not result.strip():raise ValueError('没有收到有效文本，模型可能不支持此接口或生成未完成')
        info=_finish(call_id,cid,model,result,usage,status='completed' if is_done else 'partial')
        emit('done',info)
    except urllib.error.HTTPError as e:
        message={400:'请求参数不被支持（可能是思考强度、联网工具或模型兼容问题）',401:'OpenLux 密钥无效',402:'余额或扣费权限不足',403:'模型或工具权限不足',404:'当前接口或模型不存在',429:'供应商限流',502:'上游网关失败',503:'服务繁忙'}.get(e.code,'上游 HTTP '+str(e.code))
        if call_id:db.execute('UPDATE ai_calls SET error=?,updated_at=? WHERE id=?',(message,time.time(),call_id))
        emit('error',{'message':message,'call_id':call_id,'provider_confirmed':False})
    except Exception as e:
        message=str(e)[:350]
        if call_id:db.execute('UPDATE ai_calls SET error=?,updated_at=? WHERE id=?',(message,time.time(),call_id))
        emit('error',{'message':message,'call_id':call_id,'provider_confirmed':False})
