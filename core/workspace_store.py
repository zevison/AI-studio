from __future__ import annotations
from . import db

VALID_WORKSPACES={'global','video','replica','image'}


def get_all():
    rows=db.all_('SELECT workspace,data_json,updated_at FROM workspace_drafts ORDER BY workspace')
    out={}
    for r in rows:
        out[r['workspace']]={'data':db.loads(r.get('data_json'),{}) or {},'updated_at':r.get('updated_at')}
    return out


def get(workspace):
    if workspace not in VALID_WORKSPACES:return None
    r=db.one('SELECT workspace,data_json,updated_at FROM workspace_drafts WHERE workspace=?',(workspace,))
    if not r:return None
    return {'workspace':workspace,'data':db.loads(r.get('data_json'),{}) or {},'updated_at':r.get('updated_at')}


def save(workspace,data):
    if workspace not in VALID_WORKSPACES:raise ValueError('不支持的工作区')
    if not isinstance(data,dict):raise ValueError('草稿数据格式无效')
    # Only UI state / asset ids are stored. File bytes already live in data/assets.
    now=db.now()
    db.execute('''INSERT INTO workspace_drafts(workspace,data_json,updated_at) VALUES(?,?,?)
                  ON CONFLICT(workspace) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at''',
               (workspace,db.dumps(data),now))
    return {'workspace':workspace,'updated_at':now}


def clear(workspace=None):
    if workspace:
        if workspace not in VALID_WORKSPACES:raise ValueError('不支持的工作区')
        db.execute('DELETE FROM workspace_drafts WHERE workspace=?',(workspace,))
    else:
        db.execute('DELETE FROM workspace_drafts')
    return True
