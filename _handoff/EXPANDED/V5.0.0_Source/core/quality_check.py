from __future__ import annotations
import math, os, subprocess, sys, time
from pathlib import Path
from . import db, media, config


def _hidden():
    if os.name!='nt':return {}
    kw={'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)}
    try:
        si=subprocess.STARTUPINFO();si.dwFlags|=getattr(subprocess,'STARTF_USESHOWWINDOW',0);si.wShowWindow=getattr(subprocess,'SW_HIDE',0);kw['startupinfo']=si
    except Exception:pass
    return kw


def _cv2_metrics(path:Path):
    try:
        import cv2
    except Exception as e:
        return {'available':False,'reason':f'OpenCV 未安装：{e}'}
    cap=cv2.VideoCapture(str(path))
    if not cap.isOpened():return {'available':True,'ok':False,'reason':'OpenCV 无法打开结果视频'}
    fps=float(cap.get(cv2.CAP_PROP_FPS) or 0); frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step=max(1,int((fps or 24)*0.5))
    sample_idx=0; sampled=0; black=0; frozen=0; blur=0; prev=None; diffs=[]; variances=[]
    while True:
        ok,fr=cap.read()
        if not ok:break
        if sample_idx%step==0:
            sampled+=1
            gray=cv2.cvtColor(fr,cv2.COLOR_BGR2GRAY)
            mean=float(gray.mean()); var=float(cv2.Laplacian(gray,cv2.CV_64F).var());variances.append(var)
            if mean<7:black+=1
            if var<28:blur+=1
            if prev is not None:
                d=float(cv2.absdiff(gray,prev).mean());diffs.append(d)
                if d<0.35:frozen+=1
            prev=gray
        sample_idx+=1
        if sampled>=240:break
    cap.release()
    denom=max(1,sampled)
    return {
        'available':True,'ok':sampled>0,'sampled_frames':sampled,'black_ratio':round(black/denom,4),
        'frozen_ratio':round(frozen/max(1,sampled-1),4),'blur_ratio':round(blur/denom,4),
        'mean_motion_delta':round(sum(diffs)/len(diffs),3) if diffs else None,
        'median_sharpness':round(sorted(variances)[len(variances)//2],2) if variances else None,
    }


def run(path:Path, *, reference_path:Path|None=None, task_id:str|None=None, expected:dict|None=None):
    path=Path(path)
    probe=media.probe(path)
    issues=[]; warnings=[]
    if not path.exists() or path.stat().st_size<1024:
        issues.append('结果文件不存在或文件过小')
    if not probe.get('ok'):
        issues.append('无法读取生成视频的基础媒体信息')
    if probe.get('duration') and probe['duration']<1:
        issues.append('结果视频时长异常短')
    expected=expected or {}
    if expected.get('generate_audio') is True and not probe.get('has_audio'):
        issues.append('任务要求原生音频，但结果媒体未检测到音轨')
    exp_d=expected.get('duration') or expected.get('billing_duration')
    if exp_d and probe.get('duration'):
        dd=abs(float(probe['duration'])-float(exp_d))
        if dd>max(1.5,float(exp_d)*0.15):warnings.append(f'生成视频与任务目标时长差异较大（目标 {float(exp_d):.2f} 秒，实际 {float(probe["duration"]):.2f} 秒）')
    cv=_cv2_metrics(path) if path.exists() else {'available':False,'reason':'文件不存在'}
    if cv.get('available') and cv.get('ok'):
        if cv.get('black_ratio',0)>0.08:issues.append(f"检测到较多黑帧/近黑帧（约 {cv['black_ratio']*100:.1f}%）")
        if cv.get('frozen_ratio',0)>0.18:warnings.append(f"检测到较多近静止画面（约 {cv['frozen_ratio']*100:.1f}%）")
        if cv.get('blur_ratio',0)>0.35:warnings.append(f"较多采样帧清晰度偏低（约 {cv['blur_ratio']*100:.1f}%）")
    elif cv.get('reason'):
        warnings.append(cv['reason'])

    reference={}
    if reference_path and Path(reference_path).exists():
        rp=media.probe(Path(reference_path));reference={'probe':rp}
        if probe.get('duration') and rp.get('duration'):
            diff=abs(float(probe['duration'])-float(rp['duration']))
            reference['duration_delta']=round(diff,3)
            if diff>max(1.5,float(rp['duration'])*0.12):warnings.append(f'生成视频与参考视频时长差异较大（{diff:.2f} 秒）')
        if probe.get('width') and probe.get('height') and rp.get('width') and rp.get('height'):
            rr=float(rp['width'])/float(rp['height']);gr=float(probe['width'])/float(probe['height'])
            reference['ratio_delta']=round(abs(rr-gr),4)
            if abs(rr-gr)>0.18:warnings.append('生成视频与参考视频画幅差异较大')

    report={
        'task_id':task_id,'created_at':time.time(),'path':str(path),'probe':probe,'visual':cv,'reference':reference,'expected':expected,
        'pass':not issues,'issues':issues,'warnings':warnings,
        'summary':'基础质量检查通过' if not issues else '发现需要人工检查的问题',
        'note':'本地质检用于发现黑帧、冻结、清晰度、时长/画幅异常；不把模型主观质量伪装成确定事实。'
    }
    if task_id:
        db.execute('''INSERT INTO quality_reports(task_id,report_json,created_at,updated_at) VALUES(?,?,?,?)
                      ON CONFLICT(task_id) DO UPDATE SET report_json=excluded.report_json,updated_at=excluded.updated_at''',
                   (task_id,db.dumps(report),report['created_at'],report['created_at']))
    return report


def get(task_id:str):
    r=db.one('SELECT report_json FROM quality_reports WHERE task_id=?',(task_id,))
    return db.loads(r.get('report_json'),{}) if r else None
