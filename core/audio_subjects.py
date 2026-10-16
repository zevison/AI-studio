"""Reusable local audio subjects. Only local assets, no voice embedding or cloud calls."""
from __future__ import annotations
import sqlite3,time,uuid
from . import config
DB=config.DATA_DIR/'audio_subjects.db'

def _connect():
    config.DATA_DIR.mkdir(parents=True,exist_ok=True)
    cn=sqlite3.connect(DB,timeout=10);cn.row_factory=sqlite3.Row
    cn.execute('''CREATE TABLE IF NOT EXISTS subjects(
        id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,
        source_asset_id TEXT,start_sec REAL,end_sec REAL,created_at INTEGER NOT NULL)''')
    return cn

def add(asset_id,name,description='',source_asset_id=None,start_sec=None,end_sec=None):
    name=str(name or '').strip()[:40]
    if not name:raise ValueError('音频主体名称不能为空')
    description=str(description or '').strip()[:500]
    row=(uuid.uuid4().hex,asset_id,name,description,source_asset_id,start_sec,end_sec,int(time.time()))
    with _connect() as cn:cn.execute('INSERT INTO subjects VALUES(?,?,?,?,?,?,?,?)',row)
    return dict(zip(('id','asset_id','name','description','source_asset_id','start_sec','end_sec','created_at'),row))

def list_all():
    with _connect() as cn:return [dict(r) for r in cn.execute('SELECT * FROM subjects ORDER BY created_at DESC LIMIT 250')]
