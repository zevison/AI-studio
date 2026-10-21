"""User-editable local prompt library. SQLite, no credentials, no paid model requests."""
from __future__ import annotations
import csv, io, json, re, sqlite3, threading, time
from pathlib import Path
from . import config
from .prompt_presets import builtins

DB_FILE=config.DATA_DIR/'prompt_library.db'
_LOCK=threading.RLock()
CODE_RE=re.compile(r'^/[a-z][a-z0-9_-]{1,47}$')
MAX_IMPORT_BYTES=3*1024*1024
MAX_IMPORT_ROWS=1000
FIELDS=('code','name','category','description','prompt','mode','tags','source','compatibility')
ALIASES={'code':('code','指令','命令','快捷指令'),'name':('name','title','名称','中文名','标题'),'category':('category','分类'),'prompt':('prompt','提示词','完整提示词','内容'),'description':('description','说明','描述'),'mode':('mode','模式'),'tags':('tags','标签'),'source':('source','来源'),'compatibility':('compatibility','兼容情况')}


def _conn():
    config.DATA_DIR.mkdir(parents=True,exist_ok=True)
    cn=sqlite3.connect(DB_FILE,timeout=15)
    cn.row_factory=sqlite3.Row
    cn.execute('PRAGMA busy_timeout=15000')
    cn.execute('PRAGMA journal_mode=WAL')
    cn.execute('''CREATE TABLE IF NOT EXISTS prompts(
       code TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
       description TEXT NOT NULL DEFAULT '', prompt TEXT NOT NULL,
       mode TEXT NOT NULL DEFAULT 'either', tags TEXT NOT NULL DEFAULT '',
       source TEXT NOT NULL DEFAULT '', compatibility TEXT NOT NULL DEFAULT '',
       builtin INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
       deleted INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)''')
    return cn


def setup():
    with _LOCK, _conn() as cn:
        # Migration: hide unused legacy system presets, preserve every user-imported or edited row.
        keep=[p['code'] for p in builtins()]
        cn.execute('UPDATE prompts SET deleted=1 WHERE builtin=1 AND code NOT IN ('+','.join('?' for _ in keep)+')',keep)
        for p in builtins():
            cn.execute('''INSERT OR IGNORE INTO prompts
             (code,name,category,description,prompt,mode,tags,source,compatibility,builtin,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?,1,?)''',(*[p[k] for k in FIELDS],int(time.time())))


def list_prompts():
    setup()
    with _LOCK, _conn() as cn:
        rows=[dict(x) for x in cn.execute('SELECT * FROM prompts WHERE deleted=0 ORDER BY favorite DESC, category, code')]
    return rows


def get_by_code(code):
    if not isinstance(code,str):return None
    setup()
    with _LOCK, _conn() as cn:
        r=cn.execute('SELECT * FROM prompts WHERE code=? AND deleted=0',(code.lower(),)).fetchone()
        return dict(r) if r else None


def _clean(row):
    if not isinstance(row,dict):raise ValueError('指令必须为对象（键值格式）')
    found={k:next((row[a] for a in aliases if a in row and row[a] is not None),'') for k,aliases in ALIASES.items()}
    p={k:str(found[k]).strip() for k in FIELDS}
    p['code']=p['code'].lower()
    if p['code'] and not p['code'].startswith('/'):p['code']='/'+p['code']
    if not CODE_RE.fullmatch(p['code']):raise ValueError('指令必须形如 /my-command，长度 2-48，仅含英文小写字母、数字、- 或 _')
    if not p['name'] or len(p['name'])>90:raise ValueError('中文名称不能为空或超过90字符: '+p['code'])
    if not p['prompt'] or len(p['prompt'])>14000:raise ValueError('完整提示词不能为空或超过14000字符: '+p['code'])
    for k,maxlen in [('category',60),('description',1000),('tags',500),('source',200),('compatibility',120)]:
        if len(p[k])>maxlen:raise ValueError(f'{k} 字段过长: '+p['code'])
    p['category']=p['category'] or '个人导入'
    if p['mode'] not in {'image','text','either'}:p['mode']='either'
    p['source']=p['source'] or '用户自定义'
    p['compatibility']=p['compatibility'] or '待实际出图验证'
    return p


def save(row):
    setup();p=_clean(row)
    now=int(time.time())
    with _LOCK, _conn() as cn:
        # Keep a user's favorite flag; manual edits to a built-in become custom data.
        cn.execute('''INSERT INTO prompts(code,name,category,description,prompt,mode,tags,source,compatibility,builtin,updated_at,deleted)
         VALUES(?,?,?,?,?,?,?,?,?,0,?,0)
         ON CONFLICT(code) DO UPDATE SET name=excluded.name,category=excluded.category,
         description=excluded.description,prompt=excluded.prompt,mode=excluded.mode,tags=excluded.tags,
         source=excluded.source,compatibility=excluded.compatibility,builtin=0,deleted=0,updated_at=excluded.updated_at''',(*[p[k] for k in FIELDS],now))
    return get_by_code(p['code'])


def remove(code):
    setup()
    with _LOCK, _conn() as cn:
        cur=cn.execute('UPDATE prompts SET deleted=1,updated_at=? WHERE code=? AND deleted=0',(int(time.time()),code))
        if not cur.rowcount:raise ValueError('指令不存在')
    return {'deleted':code}


def favorite(code,value):
    setup()
    with _LOCK,_conn() as cn:
        cur=cn.execute('UPDATE prompts SET favorite=?,updated_at=? WHERE code=? AND deleted=0',(1 if value else 0,int(time.time()),code))
        if not cur.rowcount:raise ValueError('指令不存在')
    return {'code':code,'favorite':bool(value)}


def _decode_records(raw,fmt):
    if not isinstance(raw,str) or not raw.strip():raise ValueError('导入内容为空')
    if len(raw.encode('utf-8'))>MAX_IMPORT_BYTES:raise ValueError('导入文件超过 3MB')
    fmt=(fmt or '').lower().strip()
    if fmt=='json':
        content=json.loads(raw.lstrip('\ufeff'))
        if isinstance(content,dict):content=content.get('prompts',content.get('items',content.get('data')))
        if not isinstance(content,list):raise ValueError('JSON 应为数组，或包含 prompts 数组的对象')
        records=content
    elif fmt=='csv':
        reader=csv.DictReader(io.StringIO(raw.lstrip('\ufeff'),newline=''))
        if not reader.fieldnames:raise ValueError('CSV 缺少标题行')
        records=list(reader)
    elif fmt=='txt':
        records=[]
        for i,line in enumerate(raw.splitlines(),1):
            line=line.strip()
            if not line or line.startswith('#'):continue
            parts=line.split('|',3)
            if len(parts)!=4:raise ValueError(f'第 {i} 行格式错误：应为 指令|名称|分类|完整提示词')
            records.append(dict(zip(('code','name','category','prompt'),map(str.strip,parts))))
    else:raise ValueError('只支持 JSON、CSV、TXT')
    if len(records)>MAX_IMPORT_ROWS:raise ValueError('一次最多导入 1000 条指令，请拆分文件')
    cleaned=[]; codes=set()
    for i,row in enumerate(records,1):
        try:p=_clean(row)
        except Exception as e:raise ValueError(f'第 {i} 条错误：{e}') from e
        if p['code'] in codes:raise ValueError(f'文件内部重复指令：{p["code"]}，请先去重')
        codes.add(p['code']);cleaned.append(p)
    return cleaned


def import_records(raw,fmt='json',on_conflict='skip',preview=False):
    if on_conflict not in ('skip','overwrite'):raise ValueError('重复处理方式只能是 skip 或 overwrite')
    entries=_decode_records(raw,fmt);setup()
    with _LOCK,_conn() as cn:
        codes=[p['code'] for p in entries];existing=set()
        for start in range(0,len(codes),400):
            chunk=codes[start:start+400]
            if chunk:existing.update(row[0] for row in cn.execute('SELECT code FROM prompts WHERE code IN ('+','.join('?' for _ in chunk)+')',chunk))
        newcount=sum(p['code'] not in existing for p in entries)
        duplicate=len(entries)-newcount
        counts={'total':len(entries),'new':newcount,'duplicate':duplicate,'inserted':0,'overwritten':0,'skipped':0,'preview':bool(preview),'examples':[{'code':p['code'],'name':p['name'],'category':p['category']} for p in entries[:8]]}
        if preview:
            counts['would_insert']=newcount
            counts['would_overwrite']=duplicate if on_conflict=='overwrite' else 0
            counts['would_skip']=duplicate if on_conflict=='skip' else 0
            return counts
        now=int(time.time())
        for p in entries:
            if p['code'] in existing and on_conflict=='skip':
                counts['skipped']+=1;continue
            cn.execute('''INSERT INTO prompts(code,name,category,description,prompt,mode,tags,source,compatibility,builtin,updated_at,deleted)
             VALUES(?,?,?,?,?,?,?,?,?,0,?,0)
             ON CONFLICT(code) DO UPDATE SET name=excluded.name,category=excluded.category,description=excluded.description,
             prompt=excluded.prompt,mode=excluded.mode,tags=excluded.tags,source=excluded.source,compatibility=excluded.compatibility,
             builtin=0,deleted=0,updated_at=excluded.updated_at''',(*[p[k] for k in FIELDS],now))
            if p['code'] in existing:counts['overwritten']+=1
            else:counts['inserted']+=1
    return counts


def export_bytes(fmt):
    rows=list_prompts();entries=[{k:x[k] for k in FIELDS} for x in rows]
    if fmt=='json':return (json.dumps({'format':'seedance-prompt-library-v1','prompts':entries},ensure_ascii=False,indent=2)+'\n').encode('utf-8'),'application/json; charset=utf-8','指令库_完整导出.json'
    if fmt=='csv':
        out=io.StringIO(newline=''); w=csv.DictWriter(out,fieldnames=FIELDS);w.writeheader()
        for x in entries:
            # Prevent formula execution on Excel/LibreOffice opening exported CSV.
            safe={k:("'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else v) for k,v in x.items()}
            w.writerow(safe)
        return out.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8','指令库_完整导出.csv'
    raise ValueError('导出格式只支持 json 或 csv')
