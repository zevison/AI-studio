from __future__ import annotations
import json, os, shutil, subprocess, sys, time, urllib.request, zipfile, tempfile, statistics, math
from pathlib import Path
from . import config, db, media, smart_analysis
from .tesseract_support import find_tesseract

TOOLS_DIR=config.ROOT/'tools'/'runtime'
BIN_DIR=TOOLS_DIR/'bin'
MODELS_DIR=TOOLS_DIR/'models'
TOOLS_DIR.mkdir(parents=True,exist_ok=True); BIN_DIR.mkdir(parents=True,exist_ok=True); MODELS_DIR.mkdir(parents=True,exist_ok=True)

def _which(name):
    if name.lower() == 'tesseract':
        return find_tesseract()
    if name.lower() == 'whisperx':
        sidecar = TOOLS_DIR / 'whisperx_py313' / ('Scripts/whisperx.exe' if os.name == 'nt' else 'bin/whisperx')
        if sidecar.exists(): return str(sidecar)
    local=BIN_DIR/(name+'.exe' if os.name=='nt' and not name.endswith('.exe') else name)
    if local.exists(): return str(local)
    return shutil.which(name)

def _module(name):
    try:
        __import__(name); return True
    except Exception:return False

def tool_status():
    return {
      'ffmpeg':{'ok':bool(_which('ffmpeg') or media.ffmpeg_path()),'path':_which('ffmpeg') or media.ffmpeg_path(),'level':'required'},
      'ffprobe':{'ok':bool(_which('ffprobe')),'path':_which('ffprobe'),'level':'required'},
      'opencv':{'ok':_module('cv2'),'level':'required'},
      'scenedetect':{'ok':_module('scenedetect'),'level':'required'},
      'faster_whisper':{'ok':_module('faster_whisper'),'level':'required'},
      'whisperx':{'ok':bool(_which('whisperx')) or _module('whisperx'),'level':'recommended'},
      'tesseract':{'ok':bool(_which('tesseract')),'path':_which('tesseract'),'level':'recommended'},
      'mediapipe':{'ok':_module('mediapipe'),'level':'recommended'},
      'yolo':{'ok':_module('ultralytics'),'level':'recommended','model_ready':bool((smart_analysis.capabilities() or {}).get('yolo_model_ready')),'note':'模块可用；本地权重准备后才启用实际目标追踪'},
      'bytetrack':{'ok':_module('ultralytics'),'level':'recommended','model_ready':bool((smart_analysis.capabilities() or {}).get('yolo_model_ready')),'note':'由 Ultralytics 内置 ByteTrack 配置提供；与本地 YOLO 权重配合使用'},
    }

def doctor():
    tools=tool_status();
    required=[k for k,v in tools.items() if v.get('level')=='required']
    missing_required=[k for k in required if not tools[k]['ok']]
    missing_recommended=[k for k,v in tools.items() if v.get('level')=='recommended' and not v['ok']]
    if tools.get('yolo',{}).get('ok') and not tools.get('yolo',{}).get('model_ready',False):
        missing_recommended.append('yolo_model')
    disk=shutil.disk_usage(config.ROOT)
    checks=[
      {'name':'本地数据库','ok':config.DB_PATH.exists(),'detail':str(config.DB_PATH)},
      {'name':'素材目录可写','ok':os.access(config.ASSET_DIR,os.W_OK),'detail':str(config.ASSET_DIR)},
      {'name':'输出目录可写','ok':os.access(config.OUTPUT_DIR,os.W_OK),'detail':str(config.OUTPUT_DIR)},
      {'name':'磁盘剩余空间','ok':disk.free>5*1024**3,'detail':f'{disk.free/1024**3:.1f} GB'},
    ]
    return {'ok':not missing_required and all(x['ok'] for x in checks),'tools':tools,'checks':checks,'missing_required':missing_required,'missing_recommended':missing_recommended}

def save_workspace(data:dict):
    # browser file inputs are deliberately NOT persisted. Only stable asset IDs are.
    clean={
      'page':data.get('page') or 'video',
      'video_prompt':str(data.get('video_prompt') or '')[:30000],
      'video_asset_ids':[str(x) for x in (data.get('video_asset_ids') or [])[:50]],
      'video_ratio':data.get('video_ratio'),'video_resolution':data.get('video_resolution'),'video_duration':data.get('video_duration'),'video_audio':bool(data.get('video_audio',True)),
      'replica_instruction':str(data.get('replica_instruction') or '')[:30000],
      'replica_video_id':data.get('replica_video_id'),'replica_product_ids':[str(x) for x in (data.get('replica_product_ids') or [])[:30]],
      'image_prompt':str(data.get('image_prompt') or '')[:30000],
      'image_ratio':data.get('image_ratio'),'image_mode':data.get('image_mode') or 'text','image_source_ids':[str(x) for x in (data.get('image_source_ids') or [])[:10]],
      'deep_mode':bool(data.get('deep_mode')),'deep_parent_version_id':data.get('deep_parent_version_id'),
      'saved_at':time.time(),
    }
    db.execute("INSERT INTO user_settings(key,value_json,updated_at) VALUES('workspace_draft',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(db.dumps(clean),time.time()))
    return clean

def load_workspace():
    r=db.one("SELECT value_json FROM user_settings WHERE key='workspace_draft'")
    return db.loads(r.get('value_json'),{}) if r else {}

def clear_workspace():
    db.execute("DELETE FROM user_settings WHERE key='workspace_draft'")
    return True

def list_assets(q='',kind=''):
    sql='SELECT * FROM assets WHERE 1=1'; ps=[]
    if kind:
        sql+=' AND kind=?'; ps.append(kind)
    if q:
        sql+=' AND lower(original_name) LIKE ?'; ps.append('%'+q.lower()+'%')
    sql+=' ORDER BY created_at DESC LIMIT 500'
    return db.all_(sql,tuple(ps))

def quality_check(path:Path):
    result={'ok':True,'issues':[],'metrics':{},'tools':tool_status()}
    meta=media.probe(path); result['metrics']['media']=meta
    if not meta.get('duration'):
        result['ok']=False;result['issues'].append({'level':'error','message':'无法读取有效视频时长'});return result
    cv_ok=_module('cv2')
    if cv_ok:
        try:
            import cv2
            cap=cv2.VideoCapture(str(path)); total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0); fps=float(cap.get(cv2.CAP_PROP_FPS) or 0)
            sample_count=min(120,max(12,int(meta['duration']*2)))
            sharp=[];dark=[];dups=0;prev=None;read=0
            for i in range(sample_count):
                pos=int((max(total-1,1))*i/max(sample_count-1,1)); cap.set(cv2.CAP_PROP_POS_FRAMES,pos); ok,fr=cap.read()
                if not ok:continue
                g=cv2.cvtColor(fr,cv2.COLOR_BGR2GRAY); read+=1
                sharp.append(float(cv2.Laplacian(g,cv2.CV_64F).var())); dark.append(float(g.mean()))
                tiny=cv2.resize(g,(64,36))
                if prev is not None and float(cv2.absdiff(tiny,prev).mean())<0.8:dups+=1
                prev=tiny
            cap.release()
            if read:
                result['metrics'].update({'sample_frames':read,'sharpness_median':round(statistics.median(sharp),2),'brightness_median':round(statistics.median(dark),2),'near_duplicate_pairs':dups})
                if statistics.median(dark)<12: result['issues'].append({'level':'warn','message':'画面整体过暗，可能存在黑帧/曝光异常'})
                if statistics.median(sharp)<18: result['issues'].append({'level':'warn','message':'画面清晰度偏低或存在明显模糊'})
                if dups>max(4,read*0.45): result['issues'].append({'level':'warn','message':'检测到较多近似重复帧，可能存在卡帧/静帧'})
        except Exception as e: result['issues'].append({'level':'info','message':'OpenCV 质检未完成：'+str(e)[:180]})
    else: result['issues'].append({'level':'info','message':'未安装 OpenCV，已跳过帧级质检'})
    try:
        semantic=smart_analysis.quick_quality_semantics(path)
        result['metrics']['semantic_local_analysis']=semantic
        pose=(semantic.get('pose') or {}) if isinstance(semantic,dict) else {}
        if semantic.get('used') and pose.get('used') and pose.get('sample_frames'):
            if float(pose.get('person_presence_ratio') or 0) > 0.45 and float(pose.get('hand_presence_ratio') or 0) < 0.08:
                result['issues'].append({'level':'info','message':'检测到较多人物画面但手部证据较少；如视频包含产品操作，建议人工复查手部/产品交互连续性。'})
    except Exception as e:
        result['metrics']['semantic_local_analysis']={'used':False,'reason':f'增强质检未完成：{type(e).__name__}: {e}'}
    result['ok']=not any(x['level']=='error' for x in result['issues'])
    return result

def backup_bundle():
    # Share the WAL-safe backup implementation with migration/backup UI.
    # Keep this legacy endpoint's root directory and filename style unchanged.
    from . import backup_manager
    result=backup_manager.create_backup(include_outputs=True,
             destination_dir=config.ROOT,filename_prefix='SeedanceStudio_Backup')
    return {'path':result['path'],'name':result['name'],'size_bytes':result['size_bytes']}
