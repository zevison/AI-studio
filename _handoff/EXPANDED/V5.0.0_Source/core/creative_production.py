"""TikTok US creative-production approval workflow. Selected model is persisted per job.
Paid rendering remains gated in the existing video workbench, never from this module.
"""
from __future__ import annotations
import copy, hashlib, json, time, uuid
from . import db, hot_radar, us_marketing

H3_TEST_MODEL='minimax-video-h3'  # backwards compatible model identity
MODEL_CHOICES={
  'auto':{'name':'智能自动路由','role':'生成时按素材和任务限制自动选择','model':'auto'},
  'viduq3-turbo': {'name':'Vidu Q3 Turbo','role':'低成本效果测试','model':'viduq3-turbo'},
  H3_TEST_MODEL: {'name':'MiniMax H3','role':'真人动作对照测试','model':H3_TEST_MODEL},
  'wan-3.0': {'name':'Wan 3.0','role':'候选模型，需验证渠道','model':'wan-3.0'},
  'doubao-seedance-2-5-260628': {'name':'Seedance 2.5','role':'高质量原有工作流','model':'doubao-seedance-2-5-260628'},
}
DEFAULT_TEST_MODEL='auto'

def _known_model(model):
    m=str(model or DEFAULT_TEST_MODEL).strip()
    if m not in MODEL_CHOICES:raise ValueError('只能选择界面支持的测试模型，不能提交未知模型')
    return m

def model_choices():return [{'id':k,**v} for k,v in MODEL_CHOICES.items()]

def _actual_matches(actual,expected):
    a=str(actual or '').lower()
    e=str(expected).lower()
    if e=='viduq3-turbo':return 'viduq3' in a and 'turbo' in a
    if e==H3_TEST_MODEL:return 'h3' in a and 'minimax' in a
    if e=='wan-3.0':return 'wan-3.0' in a or 'wan3' in a
    if e.startswith('doubao-seedance'):return 'doubao-seedance-2-5' in a
    return False


def _encode(d):return json.dumps(d,ensure_ascii=False,separators=(',',':'))
def _decode(s):
    try:return json.loads(s or '{}')
    except (ValueError,TypeError):return {}
def _ts():return time.time()

def ensure_schema():
    hot_radar.ensure_schema()
    with db.tx() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS creative_video_jobs(
          id TEXT PRIMARY KEY, product_id TEXT NOT NULL, script_id TEXT NOT NULL UNIQUE,
          state TEXT NOT NULL, model TEXT NOT NULL, edited_script_json TEXT NOT NULL,
          concept_json TEXT DEFAULT '{}', test_task_id TEXT DEFAULT '', rejection_reason TEXT DEFAULT '',
          created_at REAL NOT NULL, updated_at REAL NOT NULL)''')

def _job(row):
    v=dict(row);v['edited_script']=_decode(v.pop('edited_script_json'))
    v['concept']=_decode(v.pop('concept_json'))
    return v

def all_jobs(product_id=None):
    ensure_schema()
    if product_id:rows=db.all_('SELECT * FROM creative_video_jobs WHERE product_id=? ORDER BY updated_at DESC',(product_id,))
    else:rows=db.all_('SELECT * FROM creative_video_jobs ORDER BY updated_at DESC LIMIT 80')
    return [_job(r) for r in rows]

def from_script(script_id, video_model=None):
    ensure_schema()
    script=db.one('SELECT * FROM radar_scripts WHERE id=?',(str(script_id),))
    if not script or script['status']!='completed':raise ValueError('需要先完成且通过美国市场校验的热点脚本')
    doc=_decode(script['content_json']).get('script') or {}
    # Strict on newly generated documents. Older historical scripts require a new review.
    problems=us_marketing.validate_script(doc,us_marketing.context())
    if not problems['ok']:raise ValueError('当前脚本不符合美国日期、季节或双语规则：'+'；'.join(problems['errors']))
    model=_known_model(video_model)
    prior=db.one('SELECT * FROM creative_video_jobs WHERE script_id=?',(script_id,))
    if prior:
        return _job(prior)  # repeat clicks do not silently reset an approved task or its model
    uid='adjob_'+uuid.uuid4().hex[:16];t=_ts()
    with db.tx() as c:
        c.execute('INSERT INTO creative_video_jobs(id,product_id,script_id,state,model,edited_script_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                  (uid,script['product_id'],script_id,'await_script_approval',model,_encode(doc),t,t))
    return _job(db.one('SELECT * FROM creative_video_jobs WHERE id=?',(uid,)))

def _get(id):
    ensure_schema();row=db.one('SELECT * FROM creative_video_jobs WHERE id=?',(str(id),))
    if not row:raise ValueError('制作任务不存在')
    return _job(row)

def select_model(payload):
    job=_get(payload.get('id'))
    if job['state'] not in ('await_script_approval','await_concept_approval','await_h3_test'):
        raise ValueError('已经绑定首版视频或完成审核，不能更改测试模型')
    model=_known_model(payload.get('model'))
    with db.tx() as c:
        c.execute('UPDATE creative_video_jobs SET model=?,updated_at=? WHERE id=? AND state=?',
                  (model,_ts(),job['id'],job['state']))
    return _get(job['id'])

def _set(id,expected,new_state,**columns):
    with db.tx() as c:
        args=[new_state,_ts()];s="UPDATE creative_video_jobs SET state=?,updated_at=?"
        for key,val in columns.items():
            if key not in ('edited_script_json','concept_json','test_task_id','rejection_reason'):raise ValueError('不允许修改该字段')
            s+=f',{key}=?';args.append(val)
        s+=' WHERE id=? AND state=?';args.extend([id,expected]);cur=c.execute(s,tuple(args))
        if cur.rowcount!=1:raise ValueError('阶段已发生变化，请刷新后重新审核')
    return _get(id)

def approve_script(payload):
    job=_get(payload.get('id'))
    if job['state']!='await_script_approval':raise ValueError('当前不是脚本审核阶段')
    if payload.get('confirm') is not True:raise ValueError('需要人工确认脚本')
    doc=payload.get('script')
    if not isinstance(doc,dict):raise ValueError('请提交完整的双语脚本')
    check=us_marketing.validate_script(doc,us_marketing.context())
    if not check['ok']:raise ValueError('美国市场规则未通过：'+'；'.join(check['errors']))
    # The model cannot alter core market metadata during approval.
    doc=copy.deepcopy(doc)
    return _set(job['id'],'await_script_approval','await_h3_test',edited_script_json=_encode(doc))

def concept(job):
    d=job['edited_script']; segments=d.get('script_segments') or []
    return {'format':'9:16','goal':'TikTok US商品带货','model':_known_model(job['model']),
            'scenes':[{'index':i+1,'seconds':max(1,min(15,float(v.get('duration_s') or 5))),
                 'visual_cn':str(v.get('visual_cn') or ''),'narration_en':str(v.get('narration_en') or ''),
                 'narration_zh':str(v.get('narration_zh') or '')} for i,v in enumerate(segments)],
            'note':'初步效果为分镜与制作预览，不是真实模型渲染；首版视频需要独立付费授权。'}

def approve_concept(payload):
    job=_get(payload.get('id'))
    if job['state']!='await_concept_approval':raise ValueError('请先完成脚本确认')
    if payload.get('confirm') is not True:raise ValueError('需要人工确认初步分镜效果')
    return _set(job['id'],'await_concept_approval','await_h3_test',concept_json=_encode(concept(job)))

def activate_model(id):
    """Prepare a task-local model choice without modifying global video settings."""
    job=_get(id)
    if job['state']!='await_h3_test':
        raise ValueError('需要先审核双语脚本和分镜方案')
    model=_known_model(job['model'])
    return {'selected_model':model,'effective_model':model,'paid_request_submitted':False,
      'notice':'模型已绑定到当前制作任务。视频创作会单独核对所选模型；当前不会调用付费接口。'}

def test_handoff(id):
    job=_get(id)
    if job['state']!='await_h3_test':raise ValueError('必须先确认脚本和初步分镜')
    d=job['edited_script'];segments=d.get('script_segments') or []
    prompt=(f"US TikTok Shop product creative. 9:16. Use only verified product claims. "
            f"Follow the approved script. Do not invent functions or weather.\n"
            f"Hook: {d.get('hook_en','')}\n"+
            '\n'.join(f"Shot {i+1} ({v.get('duration_s',5)}s): {v.get('narration_en','')} | Visual: {v.get('visual_cn','')}"
                    for i,v in enumerate(segments)))
    from . import viral_review
    source=db.one('SELECT source_ids_json FROM radar_scripts WHERE id=?',(job['script_id'],))
    ids=_decode(source['source_ids_json']) if source else []
    if not isinstance(ids,list):ids=[]
    reference=viral_review.status(ids[0]) if ids else {'ready':False}
    return {'id':job['id'],'model':_known_model(job['model']),'video_prompt':prompt,'ratio':'9:16',
            'reference_asset_id':reference.get('asset_id') if reference.get('ready') else None,
            'estimated_duration':sum(x['seconds'] for x in concept(job)['scenes']),
            'max_single_clip_seconds_hint':15,'multiple_clips_need_editing':sum(x['seconds'] for x in concept(job)['scenes'])>15,
            'requires_explicit_paid_preview':True,'no_paid_generation_performed':True,
            'notice':'仅准备已选视频模型的测试脚本。当前通用视频引擎尚未验证不同视频模型的参数适配；需要核实实际路由、能力与费用后才可单次授权，不能静默替换模型。'}

def bind_h3_result(payload):
    job=_get(payload.get('id'))
    if job['state']!='await_h3_test':raise ValueError('当前不在首版待制作阶段')
    task_id=str(payload.get('task_id') or '').strip()
    row=db.one('SELECT * FROM tasks WHERE id=?',(task_id,))
    if not row:raise ValueError('找不到已生成的视频任务，请先完成所选模型的首版生成')
    if row['status']!='completed':raise ValueError('首版视频尚未生成完成')
    data=_decode(row.get('request_json'))
    actual_model=str(data.get('_request_model') or data.get('model') or '')
    if job['model']=='auto':
        route=data.get('router_decision') or {}
        selected=route.get('selected_model')
        if not selected or not _actual_matches(actual_model,selected):
            raise ValueError('自动路由视频缺少可核实的路由决策或模型不一致，不能绑定首版')
        with db.tx() as c:c.execute('UPDATE creative_video_jobs SET model=?,updated_at=? WHERE id=?',(selected,_ts(),job['id']))
    elif not _actual_matches(actual_model,job['model']):
        raise ValueError('实际视频模型与当前任务所选模型不一致，不能绑定首版结果')
    if row['created_at']<job['created_at']:raise ValueError('不能绑定创建前的历史视频任务')
    return _set(job['id'],'await_h3_test','await_first_cut_review',test_task_id=task_id)

def review_first_cut(payload):
    job=_get(payload.get('id'))
    if job['state']!='await_first_cut_review':raise ValueError('需要先绑定真实完成的首版视频任务')
    approved=payload.get('approve') is True
    reason=str(payload.get('reason') or '').strip()[:500]
    if not approved and not reason:raise ValueError('退回时请说明需要修改的地方')
    return _set(job['id'],'await_first_cut_review','approved' if approved else 'revision_requested',rejection_reason=reason)
