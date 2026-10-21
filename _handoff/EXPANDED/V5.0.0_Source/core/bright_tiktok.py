"""TikTok discover by keyword using Bright Data. MANUAL paid triggers only.
No recurring worker, no chargeable automatic retries. Local quota is NOT billing authority.
"""
from __future__ import annotations
import json, time, uuid, hashlib, threading, re, urllib.request, urllib.error, urllib.parse
from . import db,secrets_store,hot_radar,config
DATASET='gd_lu702nij2f790tmv9h'
API='https://api.brightdata.com'
LOCK=threading.RLock()
POLL_LOCK=threading.RLock()
ACTIVE_SUBMITS=set()
POLL_INTERVAL=30
STALE_SUBMISSION_SECONDS=80


def schema():
 with db.tx() as c:
  c.execute('''CREATE TABLE IF NOT EXISTS bt_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL)''')
  c.execute('''CREATE TABLE IF NOT EXISTS bt_jobs(id TEXT PRIMARY KEY,product_id TEXT,quantity INTEGER,status TEXT,snapshot_id TEXT,error TEXT,terms_json TEXT,records_json TEXT,created_at REAL,updated_at REAL,actual_records INTEGER DEFAULT 0,fingerprint TEXT)''')
  c.execute('CREATE INDEX IF NOT EXISTS idx_bt_jobs_time ON bt_jobs(created_at)')
  c.execute("""CREATE TABLE IF NOT EXISTS bt_provider_errors(
        job_id TEXT NOT NULL, row_index INTEGER NOT NULL, error_code TEXT NOT NULL,
        error_message TEXT NOT NULL, created_at REAL, PRIMARY KEY(job_id,row_index))""")
  # Additive migrations: preserve every existing task, provider snapshot and billing guard.
  cols={row[1] for row in c.execute('PRAGMA table_info(bt_jobs)').fetchall()}
  new_cols={'last_polled_at':'REAL DEFAULT 0','remote_status':"TEXT DEFAULT ''",'poll_error':"TEXT DEFAULT ''",'returned_records':'INTEGER DEFAULT 0','imported_records':'INTEGER DEFAULT 0','duplicate_records':'INTEGER DEFAULT 0','phase':"TEXT DEFAULT ''",'provider_error_count':'INTEGER DEFAULT 0','raw_snapshot_file':"TEXT DEFAULT ''"}
  for name,definition in new_cols.items():
   if name not in cols:c.execute('ALTER TABLE bt_jobs ADD COLUMN '+name+' '+definition)

def cfg():
 schema();r={x['name']:json.loads(x['value']) for x in db.all_('SELECT name,value FROM bt_settings')}
 return {'free_remaining':int(r.get('free_remaining',0)),'confirmed_at':float(r.get('confirmed_at',0)),'billing_confirmed':bool(r.get('billing_confirmed',False)),'ratio_confirmed':bool(r.get('ratio_confirmed',False)),'has_key':bool(secrets_store.get('brightdata_api_key')),'dataset_id':DATASET,'safe_limit':4500}

def save_settings(data):
 schema();free=int(data.get('free_remaining',0))
 if free<0 or free>5000:raise ValueError('免费额度必须在0至5000之间')
 key=str(data.get('api_key') or '').strip()
 if key and (len(key)<16 or len(key)>600):raise ValueError('Bright Data 密钥长度不正确')
 confirmed=data.get('billing_confirmed') is True
 ratio=data.get('ratio_confirmed') is True
 at=time.time() if confirmed and ratio else 0
 with db.tx() as c:
  for k,v in [('free_remaining',free),('billing_confirmed',confirmed),('ratio_confirmed',ratio),('confirmed_at',at)]:
   c.execute('INSERT INTO bt_settings VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',(k,json.dumps(v)))
 if key:
  secrets_store.update({'brightdata_api_key':key})
 return cfg()

def _request(method,path,data=None,timeout=28):
 key=secrets_store.get('brightdata_api_key')
 if not key:raise ValueError('尚未配置 Bright Data 密钥')
 raw=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
 req=urllib.request.Request(API+path,data=raw,method=method,headers={'Authorization':'Bearer '+key,'Accept':'application/json','Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=timeout) as res: body=res.read(12_000_000)
 except urllib.error.HTTPError as err:
  body=err.read(1100).decode('utf-8','replace')
  raise ProviderError('HTTP '+str(err.code)+': '+body[:900],err.code)
 except Exception as err:raise ProviderError('网络错误/提交结果未知：'+type(err).__name__,None)
 try:return json.loads(body.decode('utf-8-sig'))
 except Exception:raise ProviderError('Bright Data 响应不是有效 JSON',None)

class ProviderError(Exception):
 def __init__(self,message,code):super().__init__(message);self.code=code


def _terms(product):
 """Use a small representative keyword pool, not every GPT-generated variant.
 Keywords are discovery leads, never substantiated product claims.
 """
 raw=[str(x).strip()[:70] for x in (product.get('keywords') or []) if str(x).strip()]
 if not raw and product.get('name') and '待上传' not in product['name']:
  raw=[product['name']]
 seen=set();chosen=[]
 # Drop exact/near-exact normalized variants; prioritize early terms that GPT ranked.
 for term in raw:
  words=re.findall(r'[a-z0-9]+',term.casefold())
  stop={'the','a','an','for','with','and','to','in','on','of'}
  normalized=' '.join(w for w in words if w not in stop)
  if not normalized or normalized in seen:continue
  tokens=set(normalized.split())
  if any(len(tokens&x)/max(len(tokens|x),1)>=0.78 for x in [set(t.split()) for t in seen]):continue
  seen.add(normalized);chosen.append(term)
  if len(chosen)>=4:break
 return chosen

def preview(product_id,quantity):
 product=next((x for x in hot_radar.products() if x['id']==product_id),None)
 if not product:raise ValueError('请选择商品')
 qty=int(quantity)
 if qty not in (10,20,50,100):raise ValueError('采集数量仅支持10、20、50、100')
 terms=_terms(product)
 if not terms:raise ValueError('商品没有足够可用的真实搜索词，先在商品知识中心完善关键词')
 q,rem=divmod(qty,len(terms));inputs=[{'search_keyword':t,'num_of_posts':q+(i<rem)} for i,t in enumerate(terms)]
 return {'terms':terms,'quantity':qty,'input':inputs,'chargeable_request_count':1,'warning':'禁止传入 country 字段：该数据集已验证拒绝该字段。'}

def _usage():
 now=time.time();month=time.strftime('%Y-%m',time.gmtime(now))
 rows=db.all_('SELECT quantity,status,created_at FROM bt_jobs WHERE status NOT IN (\'rejected\')')
 return sum(r['quantity'] for r in rows if time.strftime('%Y-%m',time.gmtime(r['created_at']))==month)

def _repair_interrupted_submissions():
 """Background submit threads are not persistent across process restarts.
 Never resend an ambiguous paid POST; flag it for manual reconciliation.
 """
 with LOCK:
  now=time.time()
  rows=db.all_("SELECT id,updated_at FROM bt_jobs WHERE status='submitting'")
  for r in rows:
   if r['id'] not in ACTIVE_SUBMITS and now-float(r['updated_at'] or 0)>STALE_SUBMISSION_SECONDS:
    db.execute('UPDATE bt_jobs SET status=?,error=?,phase=?,updated_at=? WHERE id=?',
               ('submission_unknown','后台提交已超过80秒或启动后丢失回执；无法确认服务商是否接单，请先核实 Bright Data 账单/快照，禁止重复提交。','提交回执未知',now,r['id']))


def jobs(product_id=None):
 schema();_repair_interrupted_submissions()
 if product_id:
  return [dict(r) for r in db.all_('SELECT * FROM bt_jobs WHERE product_id=? ORDER BY created_at DESC LIMIT 60',(str(product_id),))]
 return [dict(r) for r in db.all_('SELECT * FROM bt_jobs ORDER BY created_at DESC LIMIT 80')]

def submit(data):
 if data.get('confirmed') is not True:raise ValueError('需要明确确认手动采集')
 with LOCK:
  c=cfg();p=preview(str(data.get('product_id')),data.get('quantity'))
  if not c['has_key']:raise ValueError('尚未配置密钥')
  if not c['billing_confirmed'] or not c['ratio_confirmed'] or time.time()-c['confirmed_at']>1800:
   raise ValueError('请在设置里核对免费余额和扣费比例，确认超额付费关闭；确认30分钟内有效')
  used=_usage();available=min(4500-used,c['free_remaining'])
  if available<p['quantity']:raise ValueError('本次采集超出已核验免费额度或4500本地停止线')
  _repair_interrupted_submissions()
  active=db.one("SELECT id,status FROM bt_jobs WHERE product_id=? AND status IN ('submitting','running','ready','processing','submission_unknown') ORDER BY created_at DESC LIMIT 1",(str(data['product_id']),))
  if active:
   return {'id':active['id'],'status':active['status'],'reused':True,'notice':'该商品存在进行中或结果不确定的采集任务；未再次调用付费接口。'}
  fp=hashlib.sha256(json.dumps([data['product_id'],p['input']],sort_keys=True).encode()).hexdigest()
  now=time.time()
  recent=db.one('SELECT id,status FROM bt_jobs WHERE fingerprint=? AND created_at>? ORDER BY created_at DESC LIMIT 1',(fp,now-600))
  if recent:return {'id':recent['id'],'status':recent['status'],'reused':True}
  jobid='bd_'+uuid.uuid4().hex[:20]
  db.execute('INSERT INTO bt_jobs(id,product_id,quantity,status,terms_json,created_at,updated_at,fingerprint) VALUES(?,?,?,?,?,?,?,?)',
             (jobid,data['product_id'],p['quantity'],'submitting',json.dumps(p['input']),now,now,fp))
  # Only user click enters this function; background submission is a single attempt.
  ACTIVE_SUBMITS.add(jobid)
  threading.Thread(target=_submit_once,args=(jobid,p['input']),daemon=True,name='bright-once-'+jobid).start()
  return {'id':jobid,'status':'submitting','reused':False}

def _submit_once(jobid,inputs):
 try:
  path='/datasets/v3/trigger?dataset_id='+DATASET+'&format=json&include_errors=true&type=discover_new&discover_by=keyword'
  resp=_request('POST',path,{'input':inputs},timeout=32)
  sid=resp.get('snapshot_id') if isinstance(resp,dict) else None
  if not sid or not re.fullmatch('[A-Za-z0-9_-]{5,110}',str(sid)):
   db.execute('UPDATE bt_jobs SET status=?,error=?,updated_at=? WHERE id=?',('submission_unknown','未取得合法快照编号，禁止重提',time.time(),jobid));return
  db.execute('UPDATE bt_jobs SET status=?,snapshot_id=?,remote_status=?,phase=?,updated_at=? WHERE id=?',('running',sid,'starting','已接单，正在采集',time.time(),jobid))
 except ProviderError as e:
  certain=e.code in (400,401,402,403,404,422,429)
  db.execute('UPDATE bt_jobs SET status=?,error=?,updated_at=? WHERE id=?',('rejected' if certain else 'submission_unknown',str(e)[:950],time.time(),jobid))
 except Exception as e:
  db.execute('UPDATE bt_jobs SET status=?,error=?,updated_at=? WHERE id=?',('submission_unknown','提交状态未知：'+type(e).__name__,time.time(),jobid))
 finally:
  with LOCK:ACTIVE_SUBMITS.discard(jobid)

def _snapshot_file(jobid):
    # Never accept a filename from the browser. The server owns the cache path.
    return config.CACHE_DIR/'bright_snapshots'/(str(jobid)+'.json')


def _canonical_post(record):
    """A CDN URL is media, never a TikTok source post URL."""
    for candidate in (record.get('url'),record.get('post_url')):
        if not candidate or not isinstance(candidate,str):continue
        try:
            p=urllib.parse.urlsplit(candidate)
            if p.scheme!='https' or p.hostname not in ('www.tiktok.com','tiktok.com','m.tiktok.com'):
                continue
            match=re.fullmatch(r'/@([A-Za-z0-9._-]+)/video/(\d{15,25})/?',p.path)
            if match:
                return 'https://www.tiktok.com/@'+match.group(1)+'/video/'+match.group(2)
        except (ValueError,TypeError):pass
    return ''


def _count(value):
    try:
        v=int(value)
        return v if 0<=v<=10**15 else None
    except (ValueError,TypeError,OverflowError):return None


def _process_snapshot(jobid,rows,from_cache=False):
    """Local-only processing of a provider snapshot, never a new paid collection."""
    row=db.one('SELECT * FROM bt_jobs WHERE id=?',(jobid,))
    if not row or not row['snapshot_id']:raise ValueError('未找到已提交的采集快照')
    if not isinstance(rows,list):raise ValueError('快照必须为数组，不能当作已成功采集')
    safe=[];errors=[]
    for index,rec in enumerate(rows[:500]):
        if not isinstance(rec,dict):
            errors.append((index,'invalid_row','服务商返回非对象记录'));continue
        if rec.get('error') or rec.get('error_code'):
            errors.append((index,str(rec.get('error_code') or 'provider_error')[:90],str(rec.get('error') or '服务商错误')[:350]));continue
        kind=str(rec.get('post_type') or 'video').lower()
        if kind not in ('video','videos'):
            # Do not pass photo-mode posts to the video analysis pipeline.
            continue
        source=_canonical_post(rec)
        if not source:
            errors.append((index,'invalid_post','缺少真正的TikTok原始视频链接'));continue
        pid=str(rec.get('post_id') or '')
        if pid and not source.rstrip('/').endswith('/'+pid):
            errors.append((index,'post_mismatch','post_id与原帖链接不一致'));continue
        media=rec.get('video_url') or rec.get('cdn_link')
        preview=rec.get('preview_image')
        descr=str(rec.get('description') or rec.get('title') or '').strip()[:350] or 'TikTok 原帖（标题待核实）'
        subtitles=rec.get('subtitle_info') if isinstance(rec.get('subtitle_info'),list) else []
        safe.append({'url':source,'title':descr,'description':descr,
              'create_time':rec.get('create_time') or rec.get('create_date'),
              'kind':'video','viewCount':_count(rec.get('play_count')),
              'likeCount':_count(rec.get('digg_count')),
              'commentCount':_count(rec.get('comment_count')),
              'shareCount':_count(rec.get('share_count')),
              'region':rec.get('region') or '待验证','post_id':pid or source.rsplit('/',1)[-1],
              'media_url':media if isinstance(media,str) and len(media)<5000 else None,
              'cover_url':preview if isinstance(preview,str) and len(preview)<5000 else None,
              'video_duration':rec.get('video_duration'),'subtitle_info':subtitles[:6],
              'provider_origin':'bright_data'})
    inserted=0;skipped=0
    # Use existing product-aware item store, preserving past bindings & scripts.
    for chunk in [safe[i:i+100] for i in range(0,min(row['quantity'],len(safe)),100)]:
        result=hot_radar.import_tiktok_batch({'product_id':row['product_id'],'records':chunk})
        inserted+=int(result.get('inserted') or 0)
        skipped+=len(result.get('skipped') or [])
    path=_snapshot_file(jobid)
    if not from_cache:
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.partial')
        tmp.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
        tmp.replace(path)
    with db.tx() as c:
        c.execute('DELETE FROM bt_provider_errors WHERE job_id=?',(jobid,))
        for index,code,message in errors:
            c.execute('INSERT INTO bt_provider_errors(job_id,row_index,error_code,error_message,created_at) VALUES(?,?,?,?,?)',
                      (jobid,index,code,message,time.time()))
        status='ready_partial' if safe and errors else 'done' if safe else 'provider_failed' if errors else 'empty'
        # safe may include more than quantity; only quantity items actually imported.
        c.execute("""UPDATE bt_jobs SET status=?,actual_records=?,returned_records=?,imported_records=?,
         duplicate_records=?,provider_error_count=?,raw_snapshot_file=?,records_json=?,phase=?,poll_error='',updated_at=? WHERE id=?""",
         (status,min(len(safe),row['quantity']),len(rows),inserted,max(0,min(len(safe),row['quantity'])-inserted-skipped),
          len(errors),str(path),json.dumps(safe[:row['quantity']],ensure_ascii=False),
          '可用视频'+str(min(len(safe),row['quantity']))+'条，服务商异常'+str(len(errors))+'条',time.time(),jobid))
    return {'status':status,'valid_videos':min(len(safe),row['quantity']),'provider_errors':len(errors),
            'imported':inserted,'from_cache':from_cache,'paid_calls':0}


def restore_snapshot(payload):
    """Free reprocess from a previously saved snapshot. Never POST to provider."""
    jobid=str(payload.get('id') or '')
    row=db.one('SELECT id,snapshot_id FROM bt_jobs WHERE id=?',(jobid,))
    if not row or not row['snapshot_id']:raise ValueError('当前任务没有已验证的快照编号')
    path=_snapshot_file(jobid)
    if not path.is_file() or path.stat().st_size>16_000_000:raise ValueError('不存在可恢复的本地快照文件')
    return _process_snapshot(jobid,json.loads(path.read_text('utf-8')),True)


def attach_snapshot(payload):
    """Manual reconciliation of unknown paid submit; never creates a paid POST."""
    jobid=str(payload.get('id') or '')
    sid=str(payload.get('snapshot_id') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{5,110}',sid):raise ValueError('快照编号格式错误')
    with LOCK:
        row=db.one('SELECT * FROM bt_jobs WHERE id=?',(jobid,))
        if not row or row['snapshot_id'] or row['status'] not in ('submission_unknown','rejected'):
            raise ValueError('只允许为回执异常且未保存快照编号的原任务人工补录')
        if db.one('SELECT id FROM bt_jobs WHERE snapshot_id=? AND id!=?',(sid,jobid)):
            raise ValueError('快照编号已关联其他任务，不能重复使用')
        db.execute('UPDATE bt_jobs SET status=?,snapshot_id=?,phase=?,last_polled_at=0,error=?,updated_at=? WHERE id=?',
                   ('running',sid,'等待只读核实已有快照','人工补录，服务商快照真实性待只读查询',time.time(),jobid))
        return {'id':jobid,'status':'running','snapshot_id':sid,'paid_calls':0}


def job_errors(jobid):
    return db.all_('SELECT row_index,error_code,error_message FROM bt_provider_errors WHERE job_id=? ORDER BY row_index LIMIT 25',(str(jobid),))


def poll(jobid):
    """Never trigger new collection: status-only GET followed by read-only download."""
    schema();_repair_interrupted_submissions()
    with POLL_LOCK:
        row=db.one('SELECT * FROM bt_jobs WHERE id=?',(str(jobid),))
        if not row:raise ValueError('没有找到采集任务')
        if not row['snapshot_id'] or row['status'] not in ('running','ready'):
            return dict(row)
        now=time.time()
        if now-float(row['last_polled_at'] or 0)<POLL_INTERVAL:return dict(row)
        db.execute('UPDATE bt_jobs SET last_polled_at=?,poll_error=? WHERE id=?',(now,'',jobid))
        sid=urllib.parse.quote(row['snapshot_id'],safe='')
        try:
            progress=_request('GET','/datasets/v3/progress/'+sid,timeout=15)
            status=str(progress.get('status','')).lower()
            if status=='failed':
                db.execute('UPDATE bt_jobs SET status=?,remote_status=?,phase=?,error=?,updated_at=? WHERE id=?',
                           ('failed',status,'服务商采集失败','Bright Data已标记为失败，请核对快照及账单',time.time(),jobid))
            elif status in ('starting','running'):
                db.execute('UPDATE bt_jobs SET remote_status=?,phase=?,updated_at=? WHERE id=?',
                           (status,'Bright Data '+status,time.time(),jobid))
            elif status=='ready':
                db.execute('UPDATE bt_jobs SET status=?,remote_status=?,phase=?,updated_at=? WHERE id=?',
                           ('ready',status,'只读获取已存在的快照',time.time(),jobid))
                raw=_request('GET','/datasets/v3/snapshot/'+sid+'?format=json',timeout=30)
                rows=raw if isinstance(raw,list) else raw.get('data',raw.get('results')) if isinstance(raw,dict) else None
                if not isinstance(rows,list):raise ValueError('服务商快照不是JSON数组')
                _process_snapshot(jobid,rows)
            else:
                db.execute('UPDATE bt_jobs SET remote_status=?,poll_error=?,updated_at=? WHERE id=?',
                           (status,'未知远端状态：'+status[:60],time.time(),jobid))
        except Exception as exc:
            db.execute('UPDATE bt_jobs SET poll_error=?,phase=?,updated_at=? WHERE id=?',
                       ('只读查询失败：'+str(exc)[:360],'保留快照等待只读重试',time.time(),jobid))
        return dict(db.one('SELECT * FROM bt_jobs WHERE id=?',(str(jobid),)))

def balance():
 try:
  result=_request('GET','/customer/balance',timeout=12)
  return {'money_balance':result.get('balance') if isinstance(result,dict) else None,'pending_balance':result.get('pending_balance') if isinstance(result,dict) else None,'free_remaining':None,'notice':'资金余额不等于5000免费记录额度；免费额度仍须人工核对'}
 except Exception as e:return {'error':str(e)[:150],'free_remaining':None}
