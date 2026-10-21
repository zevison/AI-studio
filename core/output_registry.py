"""Retain previously configured output roots without moving or deleting media."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile


def _load(home):
    file=Path(home)/'output-roots.json'
    if not file.exists():return []
    if file.is_symlink() or file.stat().st_size>65536:raise ValueError('输出目录历史不可读取，原文件已保留')
    doc=json.loads(file.read_text(encoding='utf8'))
    if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('roots'),list) or len(doc['roots'])>128:
        raise ValueError('输出目录历史格式无效，原文件已保留')
    roots=[]
    for value in doc['roots']:
        if not isinstance(value,str) or not Path(value).is_absolute() or value.startswith(('\\\\','//')):
            raise ValueError('输出目录历史含无效路径，原文件已保留')
        roots.append(Path(value))
    return roots


def roots(home,current,default):
    try:previous=_load(home)
    except (OSError,ValueError,TypeError):previous=[]
    return list(dict.fromkeys([Path(current).resolve(),Path(default).resolve(),*previous]))


def register(home,current,default):
    # Validate old bytes before publishing; damaged history is not overwritten.
    previous=_load(home)
    values=list(dict.fromkeys([str(Path(current).resolve()),str(Path(default).resolve()),*(str(p) for p in previous)]))
    if len(values)>128:raise ValueError('输出目录历史数量超过上限，请保留历史后整理')
    home=Path(home);home.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.output-roots-',dir=home)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream:
            json.dump({'version':1,'roots':values},stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(name,home/'output-roots.json')
    finally:Path(name).unlink(missing_ok=True)


def locate(home,current,default,name):
    if not name or name in {'.','..'} or any(c in name for c in ('/','\\',':','\x00')):
        raise PermissionError('非法输出文件名')
    for root in roots(home,current,default):
        candidate=root/name
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (candidate,*candidate.parents)):
            continue
        if candidate.is_file() and candidate.resolve().parent==root.resolve():return candidate
    return None
