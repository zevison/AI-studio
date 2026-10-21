from __future__ import annotations
import json, os, re, shutil, subprocess, sys
from pathlib import Path

from .runtime_layout import resource_root
from .configuration_service import startup_options
ROOT = resource_root()
_LOCAL_PATHS = startup_options()
BUNDLED_BIN = ROOT / 'tools' / 'ffmpeg' / 'bin'
SHARED_BIN = Path(os.environ.get('LOCALAPPDATA') or str(Path.home()/'.cache')) / 'SeedanceStudio' / 'assets' / 'tools' / 'ffmpeg' / 'bin'


def _bundled_ffmpeg():
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        return p if p and Path(p).exists() else None
    except Exception:
        return None


def ffmpeg_path():
    if _LOCAL_PATHS.get('ffmpeg_path'):
        return _LOCAL_PATHS['ffmpeg_path'] if Path(_LOCAL_PATHS['ffmpeg_path']).is_file() else None
    # V3.9.0: prefer the Studio-managed Windows bundle, then system PATH, then imageio fallback.
    bundled = BUNDLED_BIN / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    if bundled.exists():
        return str(bundled)
    if getattr(sys,'frozen',False):return None
    shared = SHARED_BIN / bundled.name
    if shared.exists():
        return str(shared)
    return shutil.which('ffmpeg') or _bundled_ffmpeg()


def ffprobe_path():
    if _LOCAL_PATHS.get('ffprobe_path'):
        return _LOCAL_PATHS['ffprobe_path'] if Path(_LOCAL_PATHS['ffprobe_path']).is_file() else None
    # V3.9.0: prefer the Studio-managed FFprobe so metadata parsing does not depend on the PC PATH.
    bundled = BUNDLED_BIN / ('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    if bundled.exists():
        return str(bundled)
    if getattr(sys,'frozen',False):return None
    shared = SHARED_BIN / bundled.name
    if shared.exists():
        return str(shared)
    p = shutil.which('ffprobe')
    if p:
        return p
    ff = ffmpeg_path()
    if ff:
        sibling = Path(ff).with_name('ffprobe.exe' if Path(ff).suffix.lower() == '.exe' else 'ffprobe')
        if sibling.exists():
            return str(sibling)
    return None


def _decode(data: bytes | str | None) -> str:
    """Decode subprocess output without depending on the Windows console code page.

    This is critical for projects stored in Chinese / non-GBK paths. Using text=True on
    Windows can raise UnicodeDecodeError before we ever get FFmpeg metadata.
    """
    if data is None:
        return ''
    if isinstance(data, str):
        return data
    # FFmpeg/ffprobe output is UTF-8/ASCII in the fields we need. Replace only path bytes
    # that cannot be represented; never let locale decoding abort probing.
    return data.decode('utf-8', errors='replace')


def _hidden_subprocess_kwargs():
    # Suppress the transient black console window produced by FFmpeg / FFprobe on
    # Windows when Seedance Studio is running as a local GUI-style web app.
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


def _run_bytes(cmd, timeout=25):
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            timeout=timeout,
            **_hidden_subprocess_kwargs(),
        )
        return p.returncode, _decode(p.stdout), _decode(p.stderr), ''
    except Exception as e:
        return -1, '', '', f'{type(e).__name__}: {e}'


def _empty(backend='none', error=''):
    return {
        'duration': None, 'width': None, 'height': None, 'fps': None,
        'has_audio': False, 'ok': False, 'backend': backend, 'error': error,
        'video_codec': None, 'audio_codec': None,
    }


def _finalize(out: dict) -> dict:
    try:
        if out.get('duration') is not None:
            out['duration'] = float(out['duration'])
            if out['duration'] <= 0:
                out['duration'] = None
    except Exception:
        out['duration'] = None
    for k in ('width', 'height'):
        try:
            if out.get(k) is not None:
                out[k] = int(out[k])
                if out[k] <= 0:
                    out[k] = None
        except Exception:
            out[k] = None
    try:
        if out.get('fps') is not None:
            out['fps'] = float(out['fps'])
            if out['fps'] <= 0:
                out['fps'] = None
    except Exception:
        out['fps'] = None
    out['has_audio'] = bool(out.get('has_audio'))
    out['ok'] = bool(out.get('duration') and out.get('width') and out.get('height'))
    return out


def _probe_via_ffprobe(path: Path, exe: str) -> dict:
    out = _empty('ffprobe')
    code, stdout, stderr, exc = _run_bytes([
        exe, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)
    ], timeout=25)
    if exc:
        out['error'] = exc
        return out
    if code != 0:
        out['error'] = (stderr or stdout)[-1200:]
        return out
    try:
        j = json.loads(stdout or '{}')
    except Exception as e:
        out['error'] = f'ffprobe JSON parse failed: {e}; output={stdout[-500:]}'
        return out
    try:
        out['duration'] = float((j.get('format') or {}).get('duration') or 0) or None
    except Exception:
        pass
    for st in j.get('streams') or []:
        if st.get('codec_type') == 'video' and out.get('width') is None:
            out['video_codec'] = st.get('codec_name')
            out['width'] = st.get('width')
            out['height'] = st.get('height')
            rate = st.get('avg_frame_rate') or st.get('r_frame_rate')
            if rate and rate != '0/0':
                try:
                    a, b = str(rate).split('/', 1)
                    out['fps'] = round(float(a) / float(b), 3) if float(b) else None
                except Exception:
                    pass
            if out.get('duration') is None:
                try:
                    out['duration'] = float(st.get('duration') or 0) or None
                except Exception:
                    pass
        elif st.get('codec_type') == 'audio':
            out['has_audio'] = True
            out['audio_codec'] = st.get('codec_name')
    return _finalize(out)


def _probe_via_ffmpeg(path: Path, exe: str) -> dict:
    out = _empty('ffmpeg')
    code, stdout, stderr, exc = _run_bytes([
        exe, '-hide_banner', '-i', str(path)
    ], timeout=25)
    if exc:
        out['error'] = exc
        return out
    txt = (stderr or '') + '\n' + (stdout or '')
    m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', txt, re.I)
    if m:
        out['duration'] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    # Restrict matching to a single video-stream line where possible, so audio values cannot interfere.
    video_lines = [ln for ln in txt.splitlines() if 'Video:' in ln]
    vtxt = '\n'.join(video_lines) if video_lines else txt
    codec = re.search(r'Video:\s*([a-zA-Z0-9_]+)', vtxt)
    if codec:
        out['video_codec'] = codec.group(1).lower()
    codec = re.search(r'Audio:\s*([a-zA-Z0-9_]+)', txt)
    if codec:
        out['audio_codec'] = codec.group(1).lower()
    m = re.search(r'\b(\d{2,5})x(\d{2,5})\b', vtxt, re.I)
    if m:
        out['width'] = int(m.group(1)); out['height'] = int(m.group(2))
    fm = re.search(r'(?<!\d)(\d+(?:\.\d+)?)\s+fps\b', vtxt, re.I)
    if not fm:
        fm = re.search(r'(?<!\d)(\d+(?:\.\d+)?)\s+tbr\b', vtxt, re.I)
    if fm:
        try:
            out['fps'] = round(float(fm.group(1)), 3)
        except Exception:
            pass
    out['has_audio'] = bool(re.search(r'\bAudio:', txt, re.I))
    if not (out.get('duration') and out.get('width') and out.get('height')):
        out['error'] = 'FFmpeg executed, but basic metadata was incomplete. Tail: ' + txt[-1200:]
    return _finalize(out)


def _probe_via_opencv(path: Path) -> dict:
    """Optional last-resort metadata fallback if OpenCV is already installed.

    It is intentionally optional; no new heavyweight dependency is required for the patch.
    """
    out = _empty('opencv')
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            out['error'] = 'OpenCV could not open the video.'
            return out
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        out['fps'] = fps or None
        out['width'] = width or None
        out['height'] = height or None
        out['duration'] = (frames / fps) if fps > 0 and frames > 0 else None
        out['has_audio'] = False  # OpenCV cannot reliably report audio; do not invent it.
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
    return _finalize(out)


def probe(path: Path) -> dict:
    """Robust local media probe with non-locale-dependent decoding and fallbacks.

    Order: ffprobe -> ffmpeg stderr metadata -> optional OpenCV fallback.
    Returns diagnostics in `backend` / `error` instead of silently converting failures to zeros.
    """
    path = Path(path)
    if not path.exists():
        return _empty('none', f'file not found: {path}')

    errors = []
    audio_only = path.suffix.lower() in {'.mp3','.wav','.m4a','.aac','.flac','.ogg','.opus','.wma'}
    fp = ffprobe_path()
    if fp:
        r = _probe_via_ffprobe(path, fp)
        if r.get('ok') or (audio_only and r.get('has_audio') and r.get('duration')):
            return {**r,'ok':True}
        errors.append(f"ffprobe: {r.get('error') or 'incomplete metadata'}")

    ff = ffmpeg_path()
    if ff:
        r = _probe_via_ffmpeg(path, ff)
        if r.get('ok') or (audio_only and r.get('has_audio') and r.get('duration')):
            return {**r,'ok':True}
        errors.append(f"ffmpeg: {r.get('error') or 'incomplete metadata'}")
    else:
        errors.append('ffmpeg: executable not found')

    r = _probe_via_opencv(path)
    if r.get('ok'):
        if errors:
            r['error'] = '; '.join(errors)
        return r
    if r.get('error'):
        errors.append('opencv: ' + r['error'])

    return _empty('failed', '; '.join(errors)[-2400:])


def image_probe(path: Path) -> dict:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return {'width': int(im.width), 'height': int(im.height)}
    except Exception:
        return {'width': None, 'height': None}
