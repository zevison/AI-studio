"""Local video review and comparable cost evidence. Never calls a provider."""
from __future__ import annotations
import hashlib
import json
import math
import threading
from urllib.parse import quote
from pathlib import Path
from . import config, db

DIMENSIONS = {'product_fidelity':'商品外观与事实','temporal_consistency':'主体与画面连续性',
              'motion':'动作自然度','prompt_alignment':'脚本与指令符合度','commercial_readiness':'成片可用度'}
RULE_VERSION = 'local-review-v1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS video_evaluations(
 task_id TEXT PRIMARY KEY, identity TEXT NOT NULL, review_json TEXT NOT NULL, updated_at REAL NOT NULL)'''
_check_slot=threading.BoundedSemaphore(1)

def schema():
    db.execute(SCHEMA)

def _object(raw):
    value=db.loads(raw,{})
    return value if isinstance(value,dict) else {}

def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def _number(value):
    return float(value) if type(value) in (int,float) and math.isfinite(value) and value>=0 else None

def _output(result):
    try:
        p=Path(result.get('local_path') or '')
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(config.OUTPUT_DIR.resolve()):return None
        stat=p.stat()
        return {'url':'/outputs/'+quote(p.name),
                'file':str(p.resolve()),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}
    except (OSError,ValueError,TypeError):return None

def technical(result):
    check=result.get('quality_check')
    if not isinstance(check,dict):return 'not_checked'
    if check.get('ok') is False or any(x.get('level')=='error' for x in check.get('issues',[]) if isinstance(x,dict)):return 'failed'
    return check.get('technical_status') if check.get('technical_status') in ('passed','warning','incomplete','failed') else 'incomplete'

def case_key(req):
    # Model, routing suffix and signed URLs are deliberately excluded. Specs,
    # prompt and asset identity must match; unlike tasks are never pooled.
    prompt=req.get('user_prompt') or req.get('prompt')
    duration=_number(req.get('billing_duration') or req.get('duration'))
    if not prompt or not duration or not req.get('ratio') or not req.get('resolution'):return None
    return _hash({key:req.get(key) for key in ('mode','ratio','resolution','output_resolution','local_export_resolution',
        'asset_ids','audio_mode','audio_reference_mode','audio_voice_bindings','generate_audio','route_control','route_preference','provider')}|{'prompt':prompt,'duration':duration})[:16]

def evidence(row):
    req=_object(row.get('request_json'));result=_object(row.get('result_json'));output=_output(result)
    identity=_hash({'request':req,'result':result,'output':output})
    model=str(req.get('_request_model') or req.get('model') or '未记录')
    return {'id':row['id'],'status':row['status'],'model':model,'case_key':case_key(req),
            'instruction':str(req.get('user_prompt') or req.get('prompt') or '')[:20000],
            'mode':req.get('mode'),'local_only':req.get('mode')=='hypit',
            'technical_status':technical(result),'output_url':output['url'] if output else None,
            'identity':identity,'duration':req.get('billing_duration') or req.get('duration'),
            'elapsed_seconds':max(0,(row.get('updated_at') or 0)-(row.get('created_at') or 0)),
            'specification':' / '.join(str(req.get(k) or '未记录') for k in ('mode','ratio','resolution','duration'))}

def review(payload):
    schema()
    row=db.one("SELECT * FROM tasks WHERE id=? AND kind='video'",(str(payload.get('task_id') or ''),))
    if not row:raise ValueError('视频任务不存在')
    current=evidence(row)
    if row['status']!='completed' or not current['output_url']:raise ValueError('须先完成视频并保存到本地，再进行评审')
    if payload.get('identity')!=current['identity']:raise ValueError('任务或成片已变化，请刷新后重新评审')
    scores=payload.get('scores')
    if not isinstance(scores,dict) or set(scores)!=set(DIMENSIONS):raise ValueError('请完整填写五项评分')
    if any(type(v) is not int or not 1<=v<=5 for v in scores.values()):raise ValueError('评分须为1至5的整数')
    if type(payload.get('usable')) is not bool or type(payload.get('facts_verified')) is not bool:raise ValueError('请明确填写成片与商品事实的复核结论')
    accepted=payload['usable']
    if accepted and (not payload['facts_verified'] or min(scores.values())<3 or sum(scores.values())<20):
        raise ValueError('可用成片须商品事实通过、每项至少3分且平均至少4分')
    if accepted and current['technical_status'] not in ('passed','warning'):raise ValueError('基础质检未完成或未通过，不能登记为合格成片')
    notes=str(payload.get('notes') or '').strip()
    if not notes or len(notes)>1000:raise ValueError('请填写1至1000字的评审依据或修改原因')
    record={'rule_version':RULE_VERSION,'scores':scores,'usable':accepted,'facts_verified':payload['facts_verified'],
            'notes':notes,'review_type':'manual','average_score':sum(scores.values())/5}
    with db.tx() as c:
        c.execute('BEGIN IMMEDIATE')
        latest=c.execute('SELECT * FROM tasks WHERE id=?',(row['id'],)).fetchone()
        if not latest or evidence(dict(latest))['identity']!=current['identity']:raise ValueError('评审期间任务发生变化，请刷新')
        c.execute('INSERT INTO video_evaluations VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET identity=excluded.identity,review_json=excluded.review_json,updated_at=excluded.updated_at',
                  (row['id'],current['identity'],db.dumps(record),db.now()))
    return record

def check(payload):
    from . import production_system
    if not _check_slot.acquire(False):raise ValueError('本工作区正在本地质检，请稍后刷新')
    try:
        row=db.one("SELECT * FROM tasks WHERE id=? AND kind='video'",(str(payload.get('task_id') or ''),))
        if not row or row['status']!='completed':raise ValueError('请选择实际完成的视频任务')
        result=_object(row.get('result_json'));output=_output(result);before=evidence(row)
        if not output:raise ValueError('成片尚未保存到本地输出目录')
        if payload.get('identity')!=before['identity']:raise ValueError('任务或成片已变化，请刷新')
        result['quality_check']=production_system.quality_check(Path(output['file']),expected=_object(row.get('request_json')))
        with db.tx() as c:
            c.execute('BEGIN IMMEDIATE')
            latest=c.execute('SELECT * FROM tasks WHERE id=?',(row['id'],)).fetchone()
            if not latest or evidence(dict(latest))['identity']!=before['identity']:raise ValueError('质检期间成片发生变化，请刷新后重查')
            # Rechecking a completed artifact must not rewrite generation latency.
            c.execute('UPDATE tasks SET result_json=? WHERE id=?',(db.dumps(result),row['id']))
        return {'technical_status':technical(result),'paid_calls':0}
    finally:_check_slot.release()

def _interval(successes,n):
    if not n:return None
    z=1.96;p=successes/n;denom=1+z*z/n
    center=(p+z*z/(2*n))/denom;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return [max(0,center-half),min(1,center+half)]

def summarize(rows):
    groups={}
    for r in rows:
        if r['local_only'] or not r['case_key']:continue
        groups.setdefault((r['case_key'],r['model']),[]).append(r)
    output=[]
    for (case,model),items in groups.items():
        settled=all(r['status'] in ('completed','failed') for r in items)
        known_reviews=settled and all(r['status']=='failed' or r.get('review') is not None for r in items)
        usable=[r for r in items if r['status']=='completed' and (r.get('review') or {}).get('usable') is True]
        costs=[r['actual_cost_usd'] for r in items]
        all_costs=settled and all(_number(v) is not None for v in costs)
        total=sum(costs) if all_costs else None
        rate=len(usable)/len(items) if known_reviews else None
        output.append({'case_key':case,'model':model,'attempts':len(items),'usable':len(usable),
            'review_complete':known_reviews,'actual_cost_complete':all_costs,'total_actual_usd':total,
            'unsettled_attempts':sum(r['status'] not in ('completed','failed') for r in items),
            'usable_rate':rate,'usable_rate_95_interval':_interval(len(usable),len(items)) if known_reviews else None,
            'cost_per_usable_video_usd':total/len(usable) if total is not None and known_reviews and usable else None,
            'evidence_level':'pilot' if len(items)<5 else 'descriptive_only',
            'note':'仅同任务条件的描述性统计；样本数量或通过率不构成模型效果保证。'})
    return output

def overview():
    schema()
    tasks=db.all_("SELECT id,status,request_json,result_json,created_at,updated_at FROM tasks WHERE kind='video' ORDER BY created_at DESC,id DESC LIMIT 200")
    ids=[r['id'] for r in tasks];parameters=','.join('?' for _ in ids)
    saved={r['task_id']:r for r in db.all_('SELECT * FROM video_evaluations WHERE task_id IN ('+parameters+')',ids)} if ids else {}
    # One bulk lookup; no per-task price/network requests and no guessed costs.
    usage={}
    for u in (db.all_("SELECT task_id,actual_cost_usd FROM usage_events WHERE kind='video' AND task_id IN ("+parameters+')',ids) if ids else []):
        usage.setdefault(u['task_id'],[]).append(u['actual_cost_usd'])
    records=[]
    asset_ids=set()
    for row in tasks:
        aids=_object(row.get('request_json')).get('asset_ids')
        if isinstance(aids,list):asset_ids.update(x for x in aids[:50] if isinstance(x,str))
    refs={}
    if asset_ids:
        for a in db.all_('SELECT id,kind,original_name,stored_name FROM assets WHERE id IN ('+','.join('?' for _ in asset_ids)+')',list(asset_ids)):
            name=a.get('stored_name')
            if name and Path(name).name==name:refs[a['id']]={'name':a['original_name'],'kind':a['kind'],'url':'/public/assets/'+quote(name,safe='')}
    for row in tasks:
        r=evidence(row);old=saved.get(row['id']);r['review']=None;r['review_stale']=False
        aids=_object(row.get('request_json')).get('asset_ids')
        r['references']=[refs[x] for x in aids[:50] if isinstance(x,str) and x in refs] if isinstance(aids,list) else []
        if old:
            if old['identity']==r['identity']:r['review']=_object(old['review_json'])
            else:r['review_stale']=True
        values=usage.get(row['id'],[])
        # Several billing entries may be snapshots or components. Without an
        # event contract, summing or selecting the latest could misstate costs.
        r['actual_cost_usd']=_number(values[0]) if len(values)==1 else None
        r['cost_status']='verified_record' if r['actual_cost_usd'] is not None else 'awaiting_bill_or_reconciliation'
        records.append(r)
    return {'tasks':records,'comparisons':summarize(records),'dimensions':DIMENSIONS,'rule_version':RULE_VERSION,
            'limit':200,'protocol':protocol(),'paid_calls':0}

def protocol():
    return {'paid_generation_permitted':False,'purpose':'先验证规格和成片，再比较每条可用成片总成本；不把生成完成当作效果合格。',
        'steps':['固定同一脚本、参考素材、时长、画幅、清晰度和音频要求，逐个验证适配能力。',
                 '先用已有成片做本地质检和人工评分；未完成评审或账单缺失时，不计算合格成片成本。',
                 '以后经授权先做短镜头小样；按场景随机交错模型顺序，重复生成并保留所有失败结果。',
                 '评审先不展示模型名称；条件允许时由两位同事独立评分，分歧复核。',
                 '计入失败、退回和重做的实际扣费；先导样本只用于筛选，不宣称稳定胜出。',
                 '成片合格后再决定是否用高规格重做；保留成功镜头，本地剪辑、字幕和音轨修改优先复用。'],
        'score_anchors':'1=严重问题，2=需大改，3=需局部修改，4=可用，5=优秀；可用结论须事实通过、每项≥3且均分≥4。',
        'research_reference':'https://openaccess.thecvf.com/content/CVPR2024/html/Huang_VBench_Comprehensive_Benchmark_Suite_for_Video_Generative_Models_CVPR_2024_paper.html',
        'reference_scope':'借鉴多维评估思想；本程序没有运行VBench，不输出或冒充VBench分数。'}
