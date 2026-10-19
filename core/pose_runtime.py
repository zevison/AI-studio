"""Separate MediaPipe environment avoids conflicting OpenCV/NumPy variants."""
import json,os,subprocess
from . import config,profile_store


def python_path():
    return profile_store.BASE_DIR/'runtime/pose111/Scripts/python.exe'


def available():
    return python_path().is_file() and (profile_store.BASE_DIR/'runtime/pose111/pose-verified.json').is_file()


def analyze(frames):
    environment={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD'))}
    try:
        result=subprocess.run([str(python_path()),str(config.ROOT/'tools/pose_worker.py'),
            str(config.ROOT/'assets/mediapipe'),str(config.CACHE_DIR)],input=json.dumps({'frames':frames[:32]}),
            capture_output=True,encoding='utf8',errors='replace',timeout=90,env=environment,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired):
        return {'available':available(),'used':False,'reason':'Isolated pose process unavailable or timed out'}
    if result.returncode or len(result.stdout)>128_000:
        return {'available':True,'used':False,'reason':'Isolated MediaPipe analysis failed; see installation health report'}
    try:return json.loads(result.stdout)
    except ValueError:return {'available':True,'used':False,'reason':'Invalid isolated pose response'}
