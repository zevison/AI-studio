from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config, media
from .tesseract_support import find_tesseract, ocr_command
from .local_component_paths import whisperx_path, local_asr_status
from . import pose_runtime


PROMPT_AUDIO_WORDS = (
    '口播', '旁白', '字幕', '声音', '音频', '语音', '节奏', '台词', '说话', 'voice', 'speech', 'audio', 'caption', 'subtitle'
)
PROMPT_TRACK_WORDS = (
    '动作', '手', '人物', '产品', '拿', '握', '连接', '安装', '使用', '跟踪', '追踪', 'action', 'hand', 'person', 'product', 'track'
)


def _module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _hidden_kwargs() -> dict:
    if os.name != 'nt':
        return {}
    kw = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, 'STARTF_USESHOWWINDOW', 0)
        si.wShowWindow = getattr(subprocess, 'SW_HIDE', 0)
        kw['startupinfo'] = si
    except Exception:
        pass
    return kw


def _find_exe(name: str) -> str | None:
    if name.lower()=='tesseract':return find_tesseract()
    if name.lower()=='ffmpeg':return media.ffmpeg_path()
    if name.lower()=='ffprobe':return media.ffprobe_path()
    if name.lower() == 'whisperx':
        return whisperx_path(config.ROOT)
    bundled = config.ROOT / 'tools' / 'runtime' / 'bin' / (name + '.exe' if os.name == 'nt' else name)
    if bundled.exists():
        return str(bundled)
    if getattr(sys,'frozen',False):return None
    p = shutil.which(name)
    if p:
        return p
    return None


def capabilities() -> dict:
    yolo_model = _yolo_model_path()
    return {
        'ffmpeg': bool(media.ffmpeg_path()),
        'ffprobe': bool(media.ffprobe_path()),
        'opencv': _module('cv2'),
        'scenedetect': _module('scenedetect'),
        'faster_whisper': bool(local_asr_status().get('available')),
        'whisperx': bool(_find_exe('whisperx')) or _module('whisperx'),
        'tesseract': bool(_find_exe('tesseract')),
        'mediapipe': pose_runtime.available() or _module('mediapipe'),
        'yolo': _module('ultralytics'),
        'bytetrack': _module('ultralytics'),
        'yolo_model_ready': bool(yolo_model),
        'yolo_model_path': str(yolo_model) if yolo_model else '',
    }


def _scene_structure(path: Path, duration: float, max_scenes: int = 18) -> dict:
    if not duration:
        return {'ok': False, 'backend': 'none', 'boundaries': [0.0], 'cut_count': 0, 'shot_count': 0}
    if _module('scenedetect'):
        try:
            from scenedetect import open_video, SceneManager
            from scenedetect.detectors import ContentDetector
            video = open_video(str(path))
            manager = SceneManager()
            manager.add_detector(ContentDetector(threshold=30.0))
            manager.detect_scenes(video, show_progress=False)
            scenes = manager.get_scene_list()
            boundaries = [0.0]
            for _, ed in scenes[:-1]:
                t = float(ed.get_seconds())
                if 0.08 < t < duration - 0.08:
                    boundaries.append(round(t, 3))
            boundaries.append(round(duration, 3))
            boundaries = sorted(set(boundaries))
            if len(boundaries) - 1 > max_scenes:
                step = (len(boundaries) - 1) / max_scenes
                keep = [boundaries[0]]
                for i in range(1, max_scenes):
                    keep.append(boundaries[min(len(boundaries) - 2, max(1, round(i * step)))])
                keep.append(boundaries[-1])
                boundaries = sorted(set(keep))
            return {
                'ok': True,
                'backend': 'PySceneDetect',
                'boundaries': boundaries,
                'cut_count': max(0, len(boundaries) - 2),
                'shot_count': max(1, len(boundaries) - 1),
                'single_shot': len(boundaries) <= 2,
            }
        except Exception as e:
            scene_error = f'{type(e).__name__}: {e}'
    else:
        scene_error = 'PySceneDetect 未安装'

    # Do not invent cuts. For analysis coverage only, create time sampling beats.
    count = max(1, min(max_scenes, int(math.ceil(duration / 4.0))))
    boundaries = [round(duration * i / count, 3) for i in range(count + 1)]
    return {
        'ok': True,
        'backend': 'time_sampling_fallback',
        'boundaries': boundaries,
        'cut_count': 0,
        'shot_count': 1,
        'single_shot': True,
        'note': scene_error + '；仅建立时间采样点，不把它们当作真实切镜。',
    }


def _extract_frame(path: Path, out: Path, t: float) -> bool:
    ffmpeg = media.ffmpeg_path()
    if not ffmpeg:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-ss', f'{max(0.0, t):.3f}', '-i', str(path), '-frames:v', '1', '-vf', 'scale=720:-2', '-q:v', '3', '-y', str(out)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, **_hidden_kwargs()
        )
        return p.returncode == 0 and out.exists() and out.stat().st_size > 0
    except Exception:
        return False


def _ocr(path: Path) -> str:
    exe = _find_exe('tesseract')
    if not exe:
        return ''
    try:
        p = subprocess.run(ocr_command(exe, path), capture_output=True, timeout=25, **_hidden_kwargs())
        raw = p.stdout.decode('utf-8', errors='replace') if isinstance(p.stdout, (bytes, bytearray)) else str(p.stdout or '')
        return ' '.join(raw.split())[:500]
    except Exception:
        return ''


def _sample_frames(path: Path, sha: str, scene: dict, max_frames: int = 8) -> list[dict]:
    bounds = list(scene.get('boundaries') or [])
    if len(bounds) < 2:
        return []
    mids = []
    for i in range(len(bounds) - 1):
        st, ed = float(bounds[i]), float(bounds[i + 1])
        if ed > st + 0.05:
            mids.append((st + ed) / 2)
    if len(mids) > max_frames:
        idxs = [round(i * (len(mids) - 1) / max(1, max_frames - 1)) for i in range(max_frames)]
        mids = [mids[i] for i in sorted(set(idxs))]
    root = config.CACHE_DIR / 'smart_analysis' / (sha[:24] if sha else 'unknown') / 'frames'
    out = []
    for i, t in enumerate(mids):
        fp = root / f'frame_{i+1:02d}_{t:.2f}.jpg'
        if fp.exists() or _extract_frame(path, fp, t):
            out.append({'time': round(t, 3), 'path': str(fp)})
    return out


def _pose_summary(frames: list[dict]) -> dict:
    if frames and pose_runtime.available():
        return pose_runtime.analyze(frames)
    if not frames or not _module('mediapipe'):
        return {'available': _module('mediapipe'), 'used': False, 'reason': '无可用采样帧或未安装 MediaPipe'}
    try:
        import cv2
        import mediapipe as mp
        pose_api = getattr(getattr(mp, 'solutions', None), 'pose', None)
        hands_api = getattr(getattr(mp, 'solutions', None), 'hands', None)
        if pose_api is None or hands_api is None:
            return {'available': True, 'used': False, 'reason': '当前 MediaPipe 包未暴露 solutions.pose / solutions.hands'}
        pose_hits = 0
        hand_hits = 0
        wrists: list[tuple[float, float, float]] = []
        with pose_api.Pose(static_image_mode=True, model_complexity=0, min_detection_confidence=0.45) as pose, \
             hands_api.Hands(static_image_mode=True, max_num_hands=2, min_detection_confidence=0.45) as hands:
            for item in frames:
                img = cv2.imread(item['path'])
                if img is None:
                    continue
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                pr = pose.process(rgb)
                hr = hands.process(rgb)
                if getattr(pr, 'pose_landmarks', None):
                    pose_hits += 1
                    lm = pr.pose_landmarks.landmark
                    # MediaPipe pose landmarks 15/16 are wrists.
                    if len(lm) > 16:
                        x = (float(lm[15].x) + float(lm[16].x)) / 2
                        y = (float(lm[15].y) + float(lm[16].y)) / 2
                        wrists.append((float(item['time']), x, y))
                if getattr(hr, 'multi_hand_landmarks', None):
                    hand_hits += 1
        movement = 0.0
        if len(wrists) >= 2:
            ds = []
            for a, b in zip(wrists, wrists[1:]):
                ds.append(math.hypot(b[1] - a[1], b[2] - a[2]))
            movement = round(sum(ds), 3)
        return {
            'available': True,
            'used': True,
            'sample_frames': len(frames),
            'pose_frames': pose_hits,
            'hand_frames': hand_hits,
            'wrist_motion_index': movement,
            'person_presence_ratio': round(pose_hits / max(1, len(frames)), 2),
            'hand_presence_ratio': round(hand_hits / max(1, len(frames)), 2),
            'note': '姿态/手部结果用于动作连续性约束，不用于身份识别。',
        }
    except Exception as e:
        return {'available': True, 'used': False, 'reason': f'MediaPipe 分析失败：{type(e).__name__}: {e}'}


def _yolo_model_path() -> Path | None:
    raw = os.getenv('SEEDANCE_YOLO_MODEL', '').strip()
    if raw:
        p = Path(raw).expanduser()
        if p.exists():
            return p
    model_dir = config.ROOT / 'tools' / 'runtime' / 'models'
    for name in ('yolo11n.pt', 'yolov8n.pt', 'yolo11s.pt', 'yolov8s.pt'):
        p = model_dir / name
        if p.exists():
            return p
    # Also accept a user-prepared model in project root; never auto-download during a paid preview.
    for name in ('yolo11n.pt', 'yolov8n.pt'):
        p = config.ROOT / name
        if p.exists():
            return p
    return None


def _object_tracks(path: Path, meta: dict, max_results: int = 55) -> dict:
    if not _module('ultralytics'):
        return {'available': False, 'used': False, 'reason': '未安装 Ultralytics（YOLO + ByteTrack）'}
    model_path = _yolo_model_path()
    if not model_path:
        return {
            'available': True,
            'used': False,
            'reason': 'YOLO 已安装，但本地模型权重尚未准备；为避免分析时静默联网下载，当前跳过。',
            'model_ready': False,
        }
    try:
        from ultralytics import YOLO
        fps = float(meta.get('fps') or 25.0)
        duration = float(meta.get('duration') or 0.0)
        total_est = max(1, int(fps * max(duration, 1.0)))
        stride = max(1, int(math.ceil(total_est / max_results)))
        model = YOLO(str(model_path))
        stream = model.track(
            source=str(path),
            stream=True,
            persist=True,
            tracker='bytetrack.yaml',
            conf=0.28,
            vid_stride=stride,
            verbose=False,
            device='cpu',
        )
        tracks: dict[str, dict[str, Any]] = {}
        seen_results = 0
        names = getattr(model, 'names', {}) or {}
        for result in stream:
            if seen_results >= max_results:
                break
            t = round((seen_results * stride) / max(fps, 1e-6), 3)
            seen_results += 1
            boxes = getattr(result, 'boxes', None)
            if boxes is None or boxes.id is None:
                continue
            try:
                ids = boxes.id.int().cpu().tolist()
                cls = boxes.cls.int().cpu().tolist()
                confs = boxes.conf.cpu().tolist()
            except Exception:
                continue
            for tid, cid, cf in zip(ids, cls, confs):
                key = str(tid)
                label = names.get(cid, str(cid)) if isinstance(names, dict) else str(cid)
                cur = tracks.get(key)
                if cur is None:
                    tracks[key] = {'track_id': key, 'class': label, 'first_time': t, 'last_time': t, 'samples': 1, 'max_confidence': round(float(cf), 3)}
                else:
                    cur['last_time'] = t
                    cur['samples'] += 1
                    cur['max_confidence'] = max(cur['max_confidence'], round(float(cf), 3))
        values = sorted(tracks.values(), key=lambda x: (-x['samples'], x['first_time']))[:24]
        class_counts: dict[str, int] = {}
        for x in values:
            class_counts[x['class']] = class_counts.get(x['class'], 0) + 1
        return {
            'available': True,
            'used': True,
            'model_ready': True,
            'model': model_path.name,
            'tracker': 'ByteTrack',
            'processed_results': seen_results,
            'tracks': values,
            'class_track_counts': class_counts,
            'note': '标准 YOLO 仅追踪其可识别的通用类别；产品专属外观仍由产品参考图与生成提示约束。',
        }
    except Exception as e:
        return {'available': True, 'used': False, 'model_ready': True, 'reason': f'YOLO/ByteTrack 运行失败：{type(e).__name__}: {e}'}


def _speech_needed(purpose: str, prompt: str, meta: dict) -> bool:
    if not meta.get('has_audio'):
        return False
    if purpose == 'replica':
        return True
    p = (prompt or '').lower()
    return any(x.lower() in p for x in PROMPT_AUDIO_WORDS)


def _track_needed(purpose: str, prompt: str, mode: str) -> bool:
    if purpose == 'replica':
        return True
    if mode in ('reference', 'edit', 'extend'):
        return True
    p = (prompt or '').lower()
    return any(x.lower() in p for x in PROMPT_TRACK_WORDS)


def analyze_video(asset: dict, *, purpose: str = 'direct', prompt: str = '', mode: str = 'reference', include_speech: bool | None = None, max_frames: int = 8) -> dict:
    path = Path(asset.get('path') or '')
    if not path.exists():
        return {'ok': False, 'asset_id': asset.get('id'), 'reason': '参考视频文件不存在'}
    meta = media.probe(path)
    duration = float(meta.get('duration') or asset.get('duration') or 0.0)
    scene = _scene_structure(path, duration)
    frames = _sample_frames(path, str(asset.get('sha256') or ''), scene, max_frames=max_frames)

    # OCR is useful only when it exists; cap work to sampled frames.
    ocr_items = []
    if _find_exe('tesseract'):
        for item in frames[:6]:
            txt = _ocr(Path(item['path']))
            if txt:
                ocr_items.append({'time': item['time'], 'text': txt})

    pose = _pose_summary(frames) if _track_needed(purpose, prompt, mode) else {'available': _module('mediapipe'), 'used': False, 'reason': '当前素材/模式无需姿态分析'}
    objects = _object_tracks(path, meta) if _track_needed(purpose, prompt, mode) else {'available': _module('ultralytics'), 'used': False, 'reason': '当前素材/模式无需目标追踪'}

    speech = {'available': False, 'used': False, 'reason': '当前无需语音分析', 'segments': [], 'words': []}
    if include_speech is None:
        include_speech = _speech_needed(purpose, prompt, meta)
    if include_speech:
        try:
            # Lazy import avoids circular import at module load time.
            from . import hypit_engine
            cache_dir = config.CACHE_DIR / 'smart_analysis' / str(asset.get('sha256') or 'unknown')[:24] / 'speech'
            speech = hypit_engine.transcribe_if_available(path, cache_dir, 'if_installed')
        except Exception as e:
            speech = {'available': False, 'used': False, 'reason': f'语音分析未完成：{type(e).__name__}: {e}', 'segments': [], 'words': []}

    return {
        'ok': bool(meta.get('ok') or duration),
        'asset_id': asset.get('id'),
        'asset_name': asset.get('original_name') or asset.get('stored_name') or path.name,
        'purpose': purpose,
        'media': meta,
        'scene': scene,
        'sample_frames': [{'time': x['time']} for x in frames],
        'ocr': {'available': bool(_find_exe('tesseract')), 'used': bool(ocr_items), 'items': ocr_items},
        'pose': pose,
        'object_tracking': objects,
        'speech': {
            'available': bool(speech.get('available')),
            'used': bool(speech.get('used')),
            'engine': speech.get('engine'),
            'reason': speech.get('reason', ''),
            'segment_count': len(speech.get('segments') or []),
            'word_count': len(speech.get('words') or []),
            'segments': (speech.get('segments') or [])[:12],
        },
        'capabilities': capabilities(),
    }


def analyze_references(assets: list[dict], *, purpose: str = 'direct', prompt: str = '', mode: str = 'reference') -> dict:
    videos = [x for x in assets if x and x.get('kind') == 'video']
    if not videos:
        return {'ok': True, 'used': False, 'purpose': purpose, 'videos': [], 'summary': '当前没有视频参考素材，无需启动本地视频解析。'}
    # Analyze at most two reference videos in direct mode; viral clone has exactly one main reference.
    limit = 1 if purpose == 'replica' else 2
    results = [analyze_video(x, purpose=purpose, prompt=prompt, mode=mode) for x in videos[:limit]]
    used_tools = set()
    for r in results:
        if (r.get('scene') or {}).get('backend') == 'PySceneDetect': used_tools.add('PySceneDetect')
        if (r.get('pose') or {}).get('used'): used_tools.add('MediaPipe')
        if (r.get('object_tracking') or {}).get('used'): used_tools.add('YOLO+ByteTrack')
        if (r.get('ocr') or {}).get('used'): used_tools.add('Tesseract OCR')
        if (r.get('speech') or {}).get('used'): used_tools.add((r.get('speech') or {}).get('engine') or 'Speech')
        if (r.get('media') or {}).get('backend'): used_tools.add(str((r.get('media') or {}).get('backend')))
    return {
        'ok': all(bool(x.get('ok')) for x in results),
        'used': True,
        'purpose': purpose,
        'video_count': len(results),
        'videos': results,
        'used_tools': sorted(used_tools),
        'summary': _summary(results, used_tools),
    }


def _summary(results: list[dict], used_tools: set[str]) -> str:
    if not results:
        return '未运行本地视频分析。'
    parts = []
    for r in results:
        sc = r.get('scene') or {}
        dur = float((r.get('media') or {}).get('duration') or 0)
        shot = int(sc.get('shot_count') or 1)
        pose = r.get('pose') or {}
        obj = r.get('object_tracking') or {}
        bits = [f'{dur:.1f}s', f'{shot}个镜头' if sc.get('backend') == 'PySceneDetect' else '时间采样']
        if pose.get('used'):
            bits.append(f"人物姿态命中{pose.get('pose_frames',0)}/{pose.get('sample_frames',0)}帧")
        if obj.get('used'):
            bits.append(f"目标轨迹{len(obj.get('tracks') or [])}条")
        if (r.get('ocr') or {}).get('used'):
            bits.append(f"OCR文字{len((r.get('ocr') or {}).get('items') or [])}段")
        if (r.get('speech') or {}).get('used'):
            bits.append(f"语音{(r.get('speech') or {}).get('segment_count',0)}段")
        parts.append(' / '.join(bits))
    tool_txt = '、'.join(sorted(used_tools)) if used_tools else '基础媒体解析'
    return f"本地智能分析已完成：{'；'.join(parts)}。实际启用：{tool_txt}。"


def prompt_context(bundle: dict, max_chars: int = 1400) -> str:
    if not bundle or not bundle.get('used'):
        return ''
    lines = [
        '【本地参考分析约束｜仅辅助，不覆盖用户明确要求】',
        '以下信息来自本地参考素材分析。只用于帮助保持镜头结构、动作连续性和声音/字幕关系；用户明确要求始终优先。',
    ]
    for i, r in enumerate(bundle.get('videos') or [], 1):
        media_info = r.get('media') or {}
        sc = r.get('scene') or {}
        lines.append(f"参考视频{i}：约{float(media_info.get('duration') or 0):.2f}s；真实切点{int(sc.get('cut_count') or 0)}；结构后端{sc.get('backend') or 'unknown'}。")
        pose = r.get('pose') or {}
        if pose.get('used'):
            lines.append(f"动作：人物姿态覆盖{pose.get('person_presence_ratio',0):.0%}，手部覆盖{pose.get('hand_presence_ratio',0):.0%}，腕部运动指数{pose.get('wrist_motion_index',0)}；保持人物/手部动作前后连续。")
        obj = r.get('object_tracking') or {}
        if obj.get('used'):
            classes = obj.get('class_track_counts') or {}
            class_text = '、'.join(f'{k}×{v}' for k, v in list(classes.items())[:8])
            if class_text:
                lines.append('通用目标追踪：'+class_text+'；同一目标在相邻时间段不要无原因瞬移、消失或变形。')
        ocr = r.get('ocr') or {}
        texts = [x.get('text','') for x in (ocr.get('items') or []) if x.get('text')][:3]
        if texts:
            lines.append('画面文字证据：'+' | '.join(x[:120] for x in texts))
        speech = r.get('speech') or {}
        segs = [x.get('text','') for x in (speech.get('segments') or []) if x.get('text')][:4]
        if segs:
            lines.append('口播节奏证据：'+' | '.join(x[:120] for x in segs))
    text = '\n'.join(lines)
    return text[:max_chars]


def augment_prompt(prompt: str, bundle: dict) -> str:
    ctx = prompt_context(bundle)
    if not ctx:
        return prompt
    return (prompt.rstrip() + '\n\n' + ctx).strip()


def enhance_tracking(reference_asset: dict, segments: list[dict]) -> dict:
    """Extra tracking layer for viral-clone workflows.

    Reuses the already extracted segment keyframes for MediaPipe and performs YOLO+ByteTrack
    only when local weights exist. No network download is triggered here.
    """
    path = Path(reference_asset.get('path') or '')
    if not path.exists():
        return {'ok': False, 'reason': '参考视频不存在'}
    meta = media.probe(path)
    frames = []
    for s in segments[:12]:
        fp = s.get('keyframe_path')
        if fp and Path(fp).exists():
            frames.append({'time': round((float(s.get('start') or 0) + float(s.get('end') or 0)) / 2, 3), 'path': fp})
    pose = _pose_summary(frames)
    objects = _object_tracks(path, meta)
    return {'ok': True, 'pose': pose, 'object_tracking': objects, 'capabilities': capabilities()}


def quick_quality_semantics(path: Path) -> dict:
    meta = media.probe(path)
    if not meta.get('duration'):
        return {'used': False, 'reason': '视频元数据不可用'}
    fake_asset = {'id': 'quality', 'path': str(path), 'sha256': re.sub(r'[^a-zA-Z0-9]', '', path.name)[:24], 'kind': 'video'}
    scene = _scene_structure(path, float(meta.get('duration') or 0), max_scenes=12)
    frames = _sample_frames(path, fake_asset['sha256'], scene, max_frames=6)
    pose = _pose_summary(frames)
    # Quality checking stays lightweight: ByteTrack is not forced after every generation.
    return {'used': True, 'scene': scene, 'pose': pose}
