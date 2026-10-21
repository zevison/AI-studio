from __future__ import annotations
import importlib.util, json, os, shutil, subprocess, sys, threading, time
from pathlib import Path
from . import config, media, db
from .tesseract_support import check_tesseract

_LOCK=threading.RLock();_JOB={'running':False,'started_at':None,'finished_at':None,'exit_code':None,'log_tail':'','error':None}


def _exe_ok(path):
    if not path:return False
    try:
        p=subprocess.run([str(path),'-version'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8,creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0))
        return p.returncode==0
    except Exception:return False


def _mod(name):return importlib.util.find_spec(name) is not None


def _tesseract_path():
    result = check_tesseract()
    return result['path'] if result['available'] else None


def _whisperx_path():
    c=[shutil.which('whisperx'),Path(sys.executable).parent/'whisperx.exe',Path(sys.executable).parent/'whisperx']
    for p in c:
        if p and Path(p).exists():return str(p)
    return None


def report():
    checks=[]
    def add(key,label,level,ok,detail='',repairable=True):checks.append({'key':key,'label':label,'level':level,'ok':bool(ok),'detail':detail,'repairable':repairable})
    ff=media.ffmpeg_path();fp=media.ffprobe_path();tess=_tesseract_path();wx=_whisperx_path()
    add('ffmpeg','FFmpeg（视频处理）','required',_exe_ok(ff),str(ff or '未找到'))
    add('ffprobe','FFprobe（媒体检测）','required',_exe_ok(fp),str(fp or '未找到'))
    try:
        with db.connect() as c:c.execute('SELECT 1').fetchone();dbok=True
    except Exception as e:dbok=False;dberr=str(e)
    add('sqlite','SQLite（本地数据库）','required',dbok,'' if dbok else dberr,False)
    # Enhanced analysis is optional. Missing these modules must not imply the
    # core image/video creation API is unusable.
    add('opencv','OpenCV（计算机视觉）','recommended',_mod('cv2'),'已安装' if _mod('cv2') else '未安装')
    add('scenedetect','PySceneDetect（镜头检测）','recommended',_mod('scenedetect'),'已安装' if _mod('scenedetect') else '未安装')
    add('faster_whisper','Faster-Whisper（高速语音识别）','recommended',_mod('faster_whisper'),'已安装' if _mod('faster_whisper') else '未安装')
    add('whisperx','WhisperX（精确语音对齐）','recommended',bool(wx) or _mod('whisperx'),str(wx or ('Python 模块已安装' if _mod('whisperx') else '未安装')))
    add('tesseract','Tesseract OCR（画面文字识别）','recommended',bool(tess),str(tess or '未安装'))
    add('mediapipe','MediaPipe（人体/手部姿态）','recommended',_mod('mediapipe'),'已安装' if _mod('mediapipe') else '未安装')
    yolo_model=config.ROOT/'tools'/'models'/'yolo11n.pt';yolo_ok=_mod('ultralytics') and yolo_model.exists() and yolo_model.stat().st_size>1_000_000
    add('ultralytics','YOLO + ByteTrack（目标检测/追踪）','recommended',yolo_ok,(f'已安装 · {yolo_model}' if yolo_ok else ('Ultralytics 已安装，但本地 yolo11n.pt 模型缺失' if _mod('ultralytics') else '未安装')))
    try:
        du=shutil.disk_usage(config.ROOT);free_gb=du.free/1024**3;diskok=free_gb>=5
    except Exception:free_gb=0;diskok=True
    add('disk','本地可用磁盘空间','required',diskok,f'{free_gb:.1f} GB 可用',False)
    required=[x for x in checks if x['level']=='required'];rec=[x for x in checks if x['level']=='recommended']
    return {'version':config.APP_VERSION,'checks':checks,'required_ok':all(x['ok'] for x in required),'recommended_ok':all(x['ok'] for x in rec),
            'missing_required':[x['key'] for x in required if not x['ok']], 'missing_recommended':[x['key'] for x in rec if not x['ok']], 'repair_job':repair_status()}


def _repair_worker(mode):
    global _JOB
    log_path=config.DATA_DIR/'analysis_repair.log';script=config.ROOT/'tools'/'install_analysis_stack.py'
    try:
        p=subprocess.run([sys.executable,str(script),'--mode',mode],cwd=str(config.ROOT),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=3600)
        text=((p.stdout or '')+'\n'+(p.stderr or ''))[-16000:]
        log_path.write_text(text,encoding='utf-8')
        with _LOCK:_JOB.update({'running':False,'finished_at':time.time(),'exit_code':p.returncode,'log_tail':text[-5000:],'error':None if p.returncode==0 else '部分增强组件未安装成功；核心工作台仍可继续使用。'})
    except Exception as e:
        with _LOCK:_JOB.update({'running':False,'finished_at':time.time(),'exit_code':-1,'log_tail':'','error':str(e)})


def start_repair(mode='full'):
    global _JOB
    with _LOCK:
        if _JOB.get('running'):return dict(_JOB)
        # Installing native packages within this live process may lock cv2.pyd
        # and corrupt the virtual environment on Windows. Never mutate it live.
        _JOB={'running':False,'started_at':time.time(),'finished_at':time.time(),
              'exit_code':11,'log_tail':'请先等待付费任务结束，关闭工作台，运行 01_INSTALL.cmd。',
              'error':'当前工作台运行中。为避免 WinError 5 锁定 cv2.pyd，已阻止在线覆盖安装。',
              'mode':mode}
        return dict(_JOB)


def repair_status():
    with _LOCK:return dict(_JOB)
