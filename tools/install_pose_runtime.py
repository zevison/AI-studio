"""Install and verify an isolated MediaPipe environment; no paid API calls."""
import json,os,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENV=Path(os.environ['LOCALAPPDATA'])/'SeedanceStudio/runtime/pose111'
PY=ENV/'Scripts/python.exe'


def run(args,**kwargs):
    result=subprocess.run(list(map(str,args)),cwd=ROOT,timeout=900,**kwargs)
    if result.returncode:raise RuntimeError('Isolated pose setup failed')
    return result


def main():
    if not PY.exists():
        ENV.parent.mkdir(parents=True,exist_ok=True)
        run([sys.executable,'-m','venv',ENV])
    check=subprocess.run([str(PY),'-c',"import mediapipe as m; assert m.__version__=='1.1.1'; assert hasattr(m.tasks.vision,'PoseLandmarker')"],
        capture_output=True,timeout=40)
    if check.returncode:
        run([PY,'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
            '--constraint',ROOT/'tools/pose_constraints.txt','mediapipe==1.1.1'])
    run([PY,'-m','pip','check'])
    # Actual model construction + inference on a known blank local frame.
    cache=ROOT/'data/cache/pose-install-check';cache.mkdir(parents=True,exist_ok=True)
    image=cache/'blank.png'
    run([PY,'-c',"import cv2,numpy as n,sys; assert cv2.imwrite(sys.argv[1],n.zeros((256,256,3),dtype=n.uint8))",image])
    probe=run([PY,ROOT/'tools/pose_worker.py',ROOT/'assets/mediapipe',cache],
        input=json.dumps({'frames':[{'path':str(image),'time':0}]}),capture_output=True,text=True,encoding='utf8')
    doc=json.loads(probe.stdout)
    if not doc.get('used') or doc.get('pose_frames')!=0 or doc.get('hand_frames')!=0:raise RuntimeError('Pose inference self-check failed')
    (ENV/'pose-verified.json').write_text(json.dumps(doc),encoding='utf8')
    print(json.dumps(doc));return 0


if __name__=='__main__':raise SystemExit(main())
