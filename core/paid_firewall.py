from __future__ import annotations
from pathlib import Path
from . import media


def inspect(payload:dict, specs:list[dict]):
    checks=[]
    def add(name,ok,detail='',blocking=False):checks.append({'name':name,'ok':bool(ok),'detail':detail,'blocking':bool(blocking)})
    media_ok=True
    for s in specs or []:
        a=s.get('asset') or {};p=Path(a.get('path') or '')
        ok=p.is_file() and p.stat().st_size>0
        media_ok=media_ok and ok
        if not ok:add('本地参考素材完整性',False,f"素材不存在：{a.get('original_name') or a.get('id')}",True)
    if not specs:add('本地参考素材完整性',True,'本次任务无本地参考素材')
    elif media_ok:add('本地参考素材完整性',True,f'已验证 {len(specs)} 个素材')
    if specs:
        ff=media.ffmpeg_path();add('FFmpeg 前置能力',bool(ff),str(ff or '未找到'),True)
    else:add('FFmpeg 前置能力',True,'文生视频无需参考素材解析')
    add('付费创建自动重试',True,'固定为 0 次；提交结果不确定时禁止自动重提')
    add('幂等键',True,'本次请求使用指纹作为 Idempotency-Key（幂等键）')
    blocking=[x for x in checks if x['blocking'] and not x['ok']]
    return {'pass':not blocking,'checks':checks,'blocking':blocking}
