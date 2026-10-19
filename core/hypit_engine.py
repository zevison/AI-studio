from __future__ import annotations
import json, math, os, re, shutil, subprocess, sys, time
from pathlib import Path
from .tesseract_support import find_tesseract
from . import config, media, hypit_policy, smart_analysis

ENGINE_NAME='Hypit-inspired native workflow engine'
ENGINE_VERSION='1.3'


def _hidden_subprocess_kwargs():
    # Windows GUI / silent-start mode:
    # FFmpeg / FFprobe / Tesseract / WhisperX are console executables.
    # Without CREATE_NO_WINDOW they can flash a black console window for a fraction
    # of a second during local analysis. This keeps analysis fully background-only.
    if os.name != 'nt':
        return {}
    kwargs={'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)}
    try:
        si=subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess,'STARTF_USESHOWWINDOW',0)
        si.wShowWindow = getattr(subprocess,'SW_HIDE',0)
        kwargs['startupinfo']=si
    except Exception:
        pass
    return kwargs


def _run(cmd, timeout=120):
    try:
        p=subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            **_hidden_subprocess_kwargs(),
        )
        return p.returncode,(p.stdout or ''),(p.stderr or '')
    except Exception as e:
        return -1,'',str(e)


def detect_scenes(path:Path, duration:float, threshold=0.30, max_scenes=24):
    # V3.9.0: prefer PySceneDetect when installed; FFmpeg remains a deterministic fallback.
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        video=open_video(str(path)); manager=SceneManager(); manager.add_detector(ContentDetector(threshold=max(8.0,min(45.0,float(threshold)*100.0))))
        manager.detect_scenes(video,show_progress=False); scenes=manager.get_scene_list()
        clean=[]
        for st,ed in scenes[:-1]:
            t=float(ed.get_seconds())
            if 0.18<t<float(duration or 0)-0.18: clean.append(round(t,3))
        if len(clean)>max_scenes-1: clean=clean[:max_scenes-1]
        return {'ok':True,'boundaries':[0.0]+clean+[round(float(duration or 0),3)],'cut_count':len(clean),'shot_count':len(clean)+1,'single_shot':bool(duration and not clean),'threshold':threshold,'backend':'PySceneDetect'}
    except Exception:
        pass
    ffmpeg=media.ffmpeg_path()
    if not ffmpeg or not duration:
        return {'ok':False,'boundaries':[0.0,float(duration or 0)],'reason':'FFmpeg 不可用或视频时长未知'}
    # FFmpeg scene detection is content-driven, not fixed-percentage slicing.
    filt=f"select='gt(scene,{float(threshold):.3f})',showinfo"
    code,out,err=_run([ffmpeg,'-hide_banner','-loglevel','info','-i',str(path),'-vf',filt,'-an','-f','null','-'],timeout=max(90,int(duration*8)+30))
    txt=out+'\n'+err
    times=[]
    for m in re.finditer(r'pts_time:([0-9]+(?:\.[0-9]+)?)',txt):
        try:
            t=float(m.group(1))
            if 0.18<t<duration-0.18: times.append(t)
        except Exception: pass
    # Deduplicate nearby cuts and cap scene count by strongest temporal coverage.
    clean=[]
    for t in sorted(set(round(x,3) for x in times)):
        if not clean or t-clean[-1] >= 0.35: clean.append(t)
    if len(clean)>max_scenes-1:
        step=len(clean)/(max_scenes-1)
        clean=[clean[min(len(clean)-1,int(i*step))] for i in range(max_scenes-1)]
        clean=sorted(set(clean))
    boundaries=[0.0]+clean+[round(duration,3)]
    detector_ok = code in (0,255) or bool(clean)
    return {
        'ok':detector_ok,
        'boundaries':boundaries,
        'cut_count':len(clean),
        'shot_count':(len(clean)+1) if duration else 0,
        'single_shot':bool(duration and len(clean)==0 and detector_ok),
        'threshold':threshold,
        'raw_matches':len(times)
    }


def detect_silence(path:Path, duration:float):
    ffmpeg=media.ffmpeg_path()
    if not ffmpeg:return {'ok':False,'intervals':[],'reason':'FFmpeg 不可用'}
    code,out,err=_run([ffmpeg,'-hide_banner','-nostats','-i',str(path),'-af','silencedetect=noise=-35dB:d=0.25','-f','null','-'],timeout=max(90,int((duration or 10)*6)+30))
    txt=out+'\n'+err; starts=[]; ends=[]
    for m in re.finditer(r'silence_start:\s*([0-9.]+)',txt):
        try:starts.append(float(m.group(1)))
        except:pass
    for m in re.finditer(r'silence_end:\s*([0-9.]+)',txt):
        try:ends.append(float(m.group(1)))
        except:pass
    intervals=[]
    for i,st in enumerate(starts):
        ed=ends[i] if i<len(ends) else float(duration or st)
        if ed>st:intervals.append({'start':round(st,3),'end':round(ed,3)})
    return {'ok':code in (0,255) or bool(intervals),'intervals':intervals}


def _interval_overlap(a0,a1,b0,b1):
    return max(0.0,min(a1,b1)-max(a0,b0))


def _silence_ratio(st,ed,silences):
    if ed<=st:return 0.0
    x=sum(_interval_overlap(st,ed,s['start'],s['end']) for s in silences)
    return max(0.0,min(1.0,x/(ed-st)))


def extract_frame(path:Path, out:Path, t:float):
    ffmpeg=media.ffmpeg_path(); out.parent.mkdir(parents=True,exist_ok=True)
    if not ffmpeg:return False
    code,_,_=_run([ffmpeg,'-hide_banner','-loglevel','error','-ss',f'{max(0,t):.3f}','-i',str(path),'-frames:v','1','-vf','scale=720:-2','-q:v','3','-y',str(out)],timeout=45)
    return code==0 and out.exists() and out.stat().st_size>0


def ocr_frame(path:Path):
    exe=find_tesseract()
    if not exe:return {'available':False,'text':''}
    code,out,err=_run([exe,str(path),'stdout','--psm','6'],timeout=25)
    text=' '.join((out or '').split())[:600]
    return {'available':True,'text':text,'ok':code==0}


def _whisperx_exe():
    current=Path(sys.executable).parent/('whisperx.exe' if os.name=='nt' else 'whisperx')
    if current.is_file():return str(current)
    sidecar = config.ROOT / 'tools' / 'runtime' / 'whisperx_py313' / ('Scripts/whisperx.exe' if os.name == 'nt' else 'bin/whisperx')
    if sidecar.exists():
        return str(sidecar)
    return shutil.which('whisperx')


def whisperx_status():
    exe = _whisperx_exe()
    return {'available':bool(exe),'binary':exe,'isolated_py313':bool(exe and 'whisperx_py313' in exe)}


def faster_whisper_status():
    try:
        import faster_whisper  # noqa: F401
        return {'available':True,'engine':'faster-whisper'}
    except Exception:
        return {'available':False,'engine':'faster-whisper'}


def transcribe_if_available(path:Path, cache_dir:Path, mode='if_installed'):
    if mode=='off':
        return {'available':False,'used':False,'engine':None,'words':[],'segments':[],'reason':'关闭'}
    info=media.probe(path)
    if info.get('ok') and info.get('has_audio') is False:
        return {'available':False,'used':False,'engine':None,'words':[],'segments':[],'reason':'视频没有音轨，已跳过语音模型加载'}
    wx=_whisperx_exe()
    fw=faster_whisper_status().get('available')
    if mode=='off':
        return {'available':bool(wx or fw),'used':False,'engine':None,'words':[],'segments':[],'reason':'关闭'}
    cache_dir.mkdir(parents=True,exist_ok=True)

    # 1) Prefer WhisperX when the user has installed it, matching Hypit-style word alignment.
    if wx:
        cmd=[wx,str(path),'--output_dir',str(cache_dir),'--output_format','json','--compute_type','int8']
        code,out,err=_run(cmd,timeout=900)
        candidates=list(cache_dir.glob('*.json'))
        if code==0 and candidates:
            try:
                data=json.loads(max(candidates,key=lambda p:p.stat().st_mtime).read_text(encoding='utf-8'))
                words=[]; segs=[]
                for si,seg in enumerate(data.get('segments') or []):
                    item={'index':si+1,'start':seg.get('start'),'end':seg.get('end'),'text':(seg.get('text') or '').strip()}
                    segs.append(item)
                    for w in seg.get('words') or []:
                        if w.get('start') is None or w.get('end') is None:continue
                        words.append({'id':f'w{len(words)+1}','start':round(float(w['start']),3),'end':round(float(w['end']),3),'word':(w.get('word') or '').strip()})
                if words or segs:
                    return {'available':True,'used':True,'engine':'WhisperX','words':words,'segments':segs,'reason':''}
            except Exception:
                pass
        wx_error=('WhisperX 本地执行失败：'+(err or out)[-350:])
    else:
        wx_error='未安装 WhisperX'

    # 2) Lightweight local fallback. It still provides word timestamps, so the workflow can use word anchors.
    if fw:
        try:
            from faster_whisper import WhisperModel
            model_name='small'
            model=WhisperModel(model_name,device='cpu',compute_type='int8')
            seg_iter,info=model.transcribe(str(path),word_timestamps=True,vad_filter=True,beam_size=3)
            words=[]; segs=[]
            for si,seg in enumerate(seg_iter):
                segs.append({'index':si+1,'start':round(float(seg.start),3),'end':round(float(seg.end),3),'text':(seg.text or '').strip()})
                for w in (seg.words or []):
                    if w.start is None or w.end is None:continue
                    words.append({'id':f'w{len(words)+1}','start':round(float(w.start),3),'end':round(float(w.end),3),'word':(w.word or '').strip()})
            if words or segs:
                return {'available':True,'used':True,'engine':'Faster-Whisper','words':words,'segments':segs,'reason':''}
            return {'available':True,'used':False,'engine':'Faster-Whisper','words':[],'segments':[],'reason':'本地转写没有识别到有效语音'}
        except Exception as e:
            return {'available':True,'used':False,'engine':'Faster-Whisper','words':[],'segments':[],'reason':f'Faster-Whisper 本地执行失败：{e}'}

    return {'available':False,'used':False,'engine':None,'words':[],'segments':[],'reason':wx_error+'；未安装 Faster-Whisper'}

def _dedupe_boundaries(values, duration, min_gap=0.85, max_count=24):
    vals=[0.0]
    for x in sorted(set(round(float(v),3) for v in values if v is not None)):
        if x <= 0.05 or x >= duration-0.05:
            continue
        if x-vals[-1] >= min_gap:
            vals.append(x)
    if duration-vals[-1] < min_gap and len(vals)>1:
        vals[-1]=round(duration,3)
    else:
        vals.append(round(duration,3))
    if len(vals)-1 > max_count:
        step=(len(vals)-1)/max_count
        keep=[vals[0]]
        for i in range(1,max_count):
            keep.append(vals[min(len(vals)-2, max(1, round(i*step)))])
        keep.append(vals[-1])
        vals=sorted(set(keep))
    return vals


def _single_shot_beats(duration, transcript, max_segments=24):
    """Build evidence-driven structural beats inside one continuous camera shot.

    Important:
    - 0 hard cuts is a valid single-shot video, not an FFmpeg failure.
    - When local ASR has word timestamps, always try to turn long narration into
      multiple analysis beats. These beats are NOT reported as camera cuts.
    - If ASR timestamps are malformed, fall back to time windows so a long
      single shot still gets enough visual evidence coverage.
    """
    if not duration:
        return [0.0, 0.0], 'single_shot', '连续单镜头'

    segs = transcript.get('segments') or []
    words = transcript.get('words') or []
    candidates = []

    # 1) Prefer natural ASR segment / phrase endings.
    for seg in segs:
        try:
            ed = float(seg.get('end'))
            if 0.5 < ed < duration - 0.25:
                candidates.append(ed)
        except Exception:
            pass
    bounds = _dedupe_boundaries(candidates, duration, min_gap=1.2, max_count=max_segments)

    # 2) If ASR collapsed everything into one long segment, derive robust
    #    3.5~5s beats from word timestamps.
    if len(bounds) <= 2 and words:
        timed_words = []
        for w in words:
            try:
                st = float(w.get('start') or 0)
                ed = float(w.get('end') or st)
            except Exception:
                continue
            if ed > st and 0 <= st <= duration + 0.5:
                timed_words.append((st, ed))
        if timed_words:
            last_word_end = max(ed for _, ed in timed_words)
            first_word_start = min(st for st, _ in timed_words)
            span = max(0.0, min(duration, last_word_end) - first_word_start)
            target_count = max(3, min(max_segments, int(round(max(duration, span) / 4.0))))
            candidates = []
            for i in range(1, target_count):
                target = duration * i / target_count
                nearest = min(timed_words, key=lambda x: abs(x[1] - target))
                if 0.5 < nearest[1] < duration - 0.25:
                    candidates.append(nearest[1])
            bounds = _dedupe_boundaries(candidates, duration, min_gap=1.35, max_count=max_segments)

    if transcript.get('used') and len(bounds) > 2:
        return bounds, 'speech_beats', '连续单镜头内按口播/逐词时间轴建立分析节拍'

    # 3) No usable speech structure: create clearly labelled time-analysis
    #    windows for visual coverage. They are NOT camera cuts.
    if duration > 7.0:
        target_count = max(3, min(max_segments, int(round(duration / 4.0))))
        candidates = [duration * i / target_count for i in range(1, target_count)]
        bounds = _dedupe_boundaries(candidates, duration, min_gap=1.8, max_count=max_segments)
        return bounds, 'time_beats', '连续单镜头内时间节拍回退（非真实镜头切换）'

    return [0.0, round(duration, 3)], 'single_shot', '连续单镜头'

def _nearest_word(words,t,side='start'):
    if not words:return None
    key='start' if side=='start' else 'end'
    w=min(words,key=lambda x:abs(float(x.get(key) or 0)-t))
    return {'id':w['id'],'word':w.get('word',''),'time':w.get(key)}


def _infer_role(index,total,st,ed,silence_ratio,cut_density,text):
    # These are explicitly candidates, not semantic facts. Evidence and confidence stay visible.
    pos=(index+0.5)/max(1,total); evidence=[]
    candidates=[]
    if index==0:
        c=0.55+min(0.18,cut_density*0.08)
        evidence.append('位于视频首段')
        if cut_density>0.7:evidence.append('首段镜头变化较快')
        candidates.append(('开头钩子候选',min(.82,c)))
    if 0.12<pos<0.78:
        c=.42+min(.22,cut_density*.10)+(0.08 if silence_ratio<.25 else 0)
        candidates.append(('主体演示/叙事推进候选',min(.78,c)))
    if pos>=0.62:
        candidates.append(('结果/证明候选',min(.68,.38+(pos-.62)*.55)))
    if index==total-1:
        candidates.append(('结尾/CTA候选',.48))
        evidence.append('位于视频末段')
    if text:
        evidence.append('检测到画面文字')
    if silence_ratio>.65:evidence.append('该段大部分为静音/低音量')
    if not candidates:candidates=[('结构过渡候选',.35)]
    candidates.sort(key=lambda x:x[1],reverse=True)
    return {'label':candidates[0][0],'confidence':round(candidates[0][1],2),'alternatives':[{'label':a,'confidence':round(b,2)} for a,b in candidates[1:3]],'evidence':evidence}


def analyze(reference_asset:dict, instruction:str='', settings:dict|None=None) -> dict:
    settings=settings or {}; path=Path(reference_asset['path']); duration=float(reference_asset.get('duration') or 0)
    width=reference_asset.get('width');height=reference_asset.get('height'); fps=reference_asset.get('fps'); has_audio=reference_asset.get('has_audio')
    if not duration:
        p=media.probe(path); duration=float(p.get('duration') or 0);width=width or p.get('width');height=height or p.get('height');fps=fps or p.get('fps');has_audio=p.get('has_audio') if has_audio is None else has_audio
    if fps is None or has_audio is None:
        pp=media.probe(path); fps=fps or pp.get('fps'); has_audio=pp.get('has_audio') if has_audio is None else has_audio
    threshold=float(settings.get('replica_scene_threshold',.30)); max_scenes=int(settings.get('replica_max_scenes',24))
    cache_root=config.CACHE_DIR/'hypit_analysis'/reference_asset['sha256'][:24];cache_root.mkdir(parents=True,exist_ok=True)
    scenes=detect_scenes(path,duration,threshold,max_scenes)
    silence=detect_silence(path,duration)
    transcript=transcribe_if_available(path,cache_root/'transcript',settings.get('replica_transcript_mode','if_installed'))
    hard_cut_count=int(scenes.get('cut_count') or 0)
    if hard_cut_count > 0:
        bounds=scenes.get('boundaries') or [0.0,duration]
        segmentation_mode='hard_cuts'
        segmentation_reason='按 FFmpeg 真实画面切换建立镜头段'
    else:
        bounds,segmentation_mode,segmentation_reason=_single_shot_beats(duration,transcript,max_scenes)
    if len(bounds)<2: bounds=[0.0,duration]
    # Defensive self-heal: a long single-shot video with usable transcript must
    # never silently collapse to one analysis segment.
    if hard_cut_count == 0 and duration > 7.0 and len(bounds) <= 2:
        target_count=max(3,min(max_scenes,int(round(duration/4.0))))
        fallback=[duration*i/target_count for i in range(1,target_count)]
        bounds=_dedupe_boundaries(fallback,duration,min_gap=1.8,max_count=max_scenes)
        segmentation_mode='time_beats'
        segmentation_reason='连续单镜头内时间节拍自愈回退（非真实镜头切换）'
    segments=[]; anchor_paths=[]; words=transcript.get('words') or []
    for i in range(len(bounds)-1):
        st=float(bounds[i]);ed=float(bounds[i+1]);
        if ed-st<.12:continue
        mid=(st+ed)/2; frame=cache_root/'frames'/f'scene_{i+1:02d}_{mid:.2f}.jpg';extract_frame(path,frame,mid)
        ocr={'available':False,'text':''}
        if settings.get('replica_ocr_mode','if_installed')!='off' and frame.exists():ocr=ocr_frame(frame)
        sr=_silence_ratio(st,ed,silence.get('intervals') or [])
        # cut_density is relative scene pace: one segment boundary per segment duration normalized to ~2s.
        cut_density=min(2.0,2.0/max(.25,ed-st))
        role=_infer_role(i,len(bounds)-1,st,ed,sr,cut_density,ocr.get('text',''))
        anchor={'type':'time','start':round(st,3),'end':round(ed,3)}
        sw=_nearest_word(words,st,'start'); ew=_nearest_word(words,ed,'end')
        if sw and ew: anchor={'type':'word','start_word':sw,'end_word':ew,'fallback_time':{'start':round(st,3),'end':round(ed,3)}}
        seg={
            'id':f'scene_{i+1:02d}','start':round(st,3),'end':round(ed,3),'duration':round(ed-st,3),'anchor':anchor,
            'keyframe_path':str(frame) if frame.exists() else '',
            'ocr_text':ocr.get('text',''),'silence_ratio':round(sr,2),'role_candidate':role,
            'source_evidence':[
                ('真实镜头边界' if segmentation_mode=='hard_cuts' else
                 '单镜头内口播/逐词节拍' if segmentation_mode=='speech_beats' else
                 '单镜头内时间节拍回退（非真实镜头切换）' if segmentation_mode=='time_beats' else
                 '连续单镜头'),
                '关键帧@'+f'{mid:.2f}s'
            ]
        }
        if transcript.get('used'):
            texts=[s.get('text','') for s in transcript.get('segments',[]) if s.get('start') is not None and _interval_overlap(st,ed,float(s.get('start') or 0),float(s.get('end') or 0))>0]
            seg['speech_text']=' '.join(texts).strip()
            if seg['speech_text']:seg['source_evidence'].append('WhisperX 本地转写')
        segments.append(seg);anchor_paths.append(str(frame) if frame.exists() else '')

    # V3.9.3: enrich the evidence with real local pose / object tracking.
    # This layer is capability-aware: MediaPipe runs only when installed; YOLO+ByteTrack
    # runs only when a local model weight exists, so analysis never triggers a hidden model download.
    try:
        smart_tracking=smart_analysis.enhance_tracking(reference_asset,segments)
    except Exception as e:
        smart_tracking={'ok':False,'reason':f'统一智能分析增强未完成：{type(e).__name__}: {e}'}
    if (smart_tracking.get('pose') or {}).get('used'):
        for seg in segments:
            seg.setdefault('source_evidence',[]).append('MediaPipe 人体/手部姿态采样')
    if (smart_tracking.get('object_tracking') or {}).get('used'):
        for seg in segments:
            seg.setdefault('source_evidence',[]).append('YOLO + ByteTrack 通用目标连续性追踪')

    keyframe_count=sum(bool(x.get('keyframe_path')) for x in segments)
    media_valid=bool(duration and width and height and fps)
    if segmentation_mode=='hard_cuts':
        visual_ready=bool(scenes.get('ok') and len(segments)>=2 and keyframe_count>=2)
    elif segmentation_mode in ('speech_beats','time_beats'):
        visual_ready=bool(scenes.get('ok') and len(segments)>=2 and keyframe_count>=2)
    else:
        visual_ready=bool(scenes.get('ok') and len(segments)==1 and keyframe_count>=1 and duration<=7.0)
    word_coverage=round(sum(1 for s in segments if s['anchor']['type']=='word')/max(1,len(segments)),2)
    evidence_score=.0
    if media_valid:evidence_score+=.20
    if scenes.get('ok'):evidence_score+=.15
    if hard_cut_count>0:evidence_score+=.20
    elif segmentation_mode=='speech_beats':evidence_score+=.15
    elif segmentation_mode=='time_beats':evidence_score+=.08
    elif segmentation_mode=='single_shot':evidence_score+=.05
    if visual_ready:evidence_score+=.20
    if silence.get('ok'):evidence_score+=.05
    if transcript.get('used'):evidence_score+=.15
    if word_coverage>0:evidence_score+=.05
    if any(s.get('ocr_text') for s in segments):evidence_score+=.05
    evidence_score=round(min(1,evidence_score),2)
    base_gate=bool(media_valid and visual_ready and evidence_score>=.65)
    word_required=bool(settings.get('replica_require_word_anchor_for_paid',False))
    word_gate=(word_coverage>0) if word_required else True
    gate=base_gate and word_gate
    if gate:
        if hard_cut_count>0:
            gate_reason=f'已检测到 {hard_cut_count} 个真实切点 / {hard_cut_count+1} 个镜头，并建立 {len(segments)} 个证据段，可进入人工确认。'
        elif segmentation_mode=='speech_beats':
            gate_reason=f'未检测到硬切镜头；已识别为连续单镜头，并依据口播/逐词时间轴建立 {len(segments)} 个分析节拍，可进入人工确认。'
        elif segmentation_mode=='time_beats':
            gate_reason=f'未检测到硬切镜头；已识别为连续单镜头，并建立 {len(segments)} 个视觉时间节拍供人工确认。'
        else:
            gate_reason='已识别为短时连续单镜头，基础视觉证据有效，可进入人工确认。'
    elif base_gate and word_required and not word_gate:
        gate_reason='已开启逐词锚点硬门槛，但当前没有可用词锚点。请启用本地逐词转写，或在设置中明确关闭该门槛。'
    elif not media_valid:
        gate_reason='基础媒体信息无效；请检查 FFmpeg/媒体读取。'
    elif scenes.get('ok') and hard_cut_count==0 and segmentation_mode=='single_shot':
        gate_reason='视频是连续单镜头且缺少足够结构锚点；这不是 FFmpeg 故障。建议启用逐词转写或补充视觉节拍分析后再确认。'
    else:
        gate_reason='媒体读取正常，但本地结构证据仍不足；请检查分析节拍与关键帧结果，不要直接做付费视频测试。'
    return {
        'schema':hypit_policy.WORKFLOW_SCHEMA,
        'engine':{'name':ENGINE_NAME,'version':ENGINE_VERSION,'principle':'完整可编辑工作流；优先词锚点，缺失时回退时间锚点；语义角色均为证据驱动候选'},
        'source':{'asset_id':reference_asset.get('id'),'sha256':reference_asset.get('sha256'),'duration':round(duration,3),'width':width,'height':height,'fps':fps,'has_audio':bool(has_audio)},
        'instruction':(instruction or '').strip(),
        'analysis':{
            'scene_detection':scenes,'segmentation':{'mode':segmentation_mode,'beat_count':len(segments),'reason':segmentation_reason},'silence_detection':{'ok':silence.get('ok'),'intervals':silence.get('intervals',[])},
            'transcript':{'available':transcript.get('available'),'used':transcript.get('used'),'engine':transcript.get('engine'),'reason':transcript.get('reason',''),'word_count':len(words),'segment_count':len(transcript.get('segments') or [])},
            'ocr_available':bool(find_tesseract()),
            'smart_tracking':smart_tracking,
            'evidence_score':evidence_score,'word_anchor_coverage':word_coverage,
            'paid_test_gate':{'pass':gate,'base_visual_pass':base_gate,'word_anchor_required':word_required,'word_anchor_pass':word_gate,'reason':gate_reason}
        },
        'tracks':{
            'footage':segments,
            'captions':[{'scene_id':s['id'],'text':s.get('ocr_text',''),'anchor':s['anchor']} for s in segments if s.get('ocr_text')],
            'dialogue':[{'scene_id':s['id'],'text':s.get('speech_text',''),'anchor':s['anchor']} for s in segments if s.get('speech_text')],
            'broll_candidates':[{'scene_id':s['id'],'anchor':s['anchor'],'confidence':0.35,'reason':'本地规则仅标记候选，不把 B-roll 当作已确认事实'} for s in segments if s['silence_ratio']>0.45 and s['duration']<4.5],
            'effects_candidates':([{'scene_id':seg['id'],'time':seg['start'],'type':'cut/transition','confidence':0.70} for seg in segments[1:]] if segmentation_mode=='hard_cuts' else []),
            'audio':[{'start':x['start'],'end':x['end'],'type':'silence_or_low_level'} for x in silence.get('intervals',[])],
            'pose_motion':(smart_tracking.get('pose') or {}),
            'object_tracks':((smart_tracking.get('object_tracking') or {}).get('tracks') or []),
        },
        'replacement_policy':{
            'preserve':list(hypit_policy.PRESERVE),
            'replace':list(hypit_policy.REPLACE),
            'rule':'替换内容与编排结构分离；改主体时不自动破坏字幕/节奏/镜头轨。'
        },
        'anchors':[
            {'label':s['role_candidate']['label'],'time':round((s['start']+s['end'])/2,2),'path':s.get('keyframe_path',''),'confidence':s['role_candidate']['confidence'],'evidence':s['role_candidate']['evidence']}
            for s in segments[:12]
        ],
        'duration':round(duration,3),
        'segments':segments,
        'rules':list(hypit_policy.PRINCIPLES)
    }
