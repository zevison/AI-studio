from __future__ import annotations
import argparse, hashlib, importlib.metadata, json, os, shutil, subprocess, sys, tempfile, urllib.request, zipfile
from urllib.error import URLError
from download_progress import download_file
from pathlib import Path
from shared_runtime import copy_previous_asset, cache_asset, ASSETS

ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'tools'
FFDIR=TOOLS/'ffmpeg'/'bin'
# The 7z build is about 34MB; the previous ZIP was 109MB over the same route.
# Pin known official 9.0.2 release assets and verify the SHA-256 before extraction.
FF_URL='https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.7z'
FF_BACKUP_URL='https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.7z'
FF_ZIP_URL='https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip'
FF_SEVEN_SHA256='4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077'
FF_ZIP_SHA256='60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
FF_SOURCES=(('Gyan.dev 7z (34 MB)',FF_URL,'7z',FF_SEVEN_SHA256),
            ('Official GitHub mirror 7z (34 MB)',FF_BACKUP_URL,'7z',FF_SEVEN_SHA256),
            ('Gyan.dev ZIP compatibility fallback (109 MB)',FF_ZIP_URL,'zip',FF_ZIP_SHA256))


def run(cmd, timeout=1800):
    print('>', ' '.join(map(str,cmd)), flush=True)
    try:
        p=subprocess.run(list(map(str,cmd)),cwd=str(ROOT),timeout=timeout)
        return p.returncode==0
    except Exception as e:
        print('[WARN]',e,flush=True);return False


def module_ok(name):
    try:__import__(name);return True
    except Exception:return False


def install_pip(specs, *, no_deps=False):
    # Never reinstall an existing native library merely to satisfy an optional feature.
    flags=['--no-deps'] if no_deps else []
    flags += ['--constraint',str(ROOT/'tools/analysis_constraints.txt')]
    return run([sys.executable,'-m','pip','install','--disable-pip-version-check',
                '--prefer-binary','--only-binary=:all:',*flags,*specs])


def studio_is_running():
    """Prevent hot replacement of .pyd / DLL files used by a running Studio."""
    candidates=[]
    runtime_port=ROOT/'.runtime_port'
    if runtime_port.exists():
        try:candidates.append(int(runtime_port.read_text(encoding='ascii').strip()))
        except (ValueError, OSError):pass
    current_url=ROOT/'data'/'current_url.txt'
    if current_url.exists():
        try:
            from urllib.parse import urlparse
            port=urlparse(current_url.read_text(encoding='utf-8').strip()).port
            if port:candidates.append(port)
        except (ValueError, OSError):pass
    for port in set(candidates):
        if not 1 <= port <= 65535:continue
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status',timeout=1) as r:
                payload=json.loads(r.read().decode('utf-8'))
            reported=(payload.get('data') or {}).get('root_dir')
            if reported and Path(reported).resolve()==ROOT.resolve():return True
        except (OSError, ValueError, KeyError, URLError):pass
    return False


def install_missing_dependencies(dist_name, _seen=None):
    """Resolve missing transitive packages without replacing managed OpenCV."""
    _seen=set() if _seen is None else _seen
    canonical=dist_name.lower().replace('_','-')
    if canonical in _seen: return True
    if len(_seen)>=200:raise RuntimeError('Analysis dependency graph exceeds bounded size')
    _seen.add(canonical)
    try:
        from pip._vendor.packaging.requirements import Requirement
        requirements=importlib.metadata.requires(dist_name) or []
        good=True
        for raw in requirements:
            req=Requirement(raw)
            if req.marker is not None and not req.marker.evaluate():continue
            dist=req.name.lower().replace('_','-')
            if dist.startswith('opencv-'):
                print('[SKIP] OpenCV 已由工作台管理；不安装第二套 cv2.pyd。')
                continue
            try:
                installed=importlib.metadata.version(req.name)
                if req.specifier and installed not in req.specifier:
                    print(f'[WARN] {req.name}={installed} 不满足 {req.specifier}；本次不覆盖升级。')
                    good=False
                if not install_missing_dependencies(req.name,_seen):good=False
                continue
            except importlib.metadata.PackageNotFoundError:
                pass
            if not install_pip([str(req)],no_deps=True):
                good=False
                print(f'[WARN] 缺少可选依赖 {req.name}，已保留原环境。')
            elif not install_missing_dependencies(req.name,_seen):
                good=False
        return good
    except Exception as exc:
        print('[WARN] 检查依赖失败:',exc)
        return False


def install_mediapipe_safely():
    # MediaPipe has its own OpenCV/NumPy dependency graph. It runs in a
    # separate environment instead of overwriting the application's cv2 DLL.
    print('[INFO] Installing/verifying isolated MediaPipe Tasks runtime ...')
    return run([sys.executable, TOOLS/'install_pose_runtime.py'])


def _validate_ffmpeg_binary(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 100_000:
        raise RuntimeError(f'FFmpeg executable missing or truncated: {path.name}')
    with path.open('rb') as stream:
        if stream.read(2)!=b'MZ':
            raise RuntimeError(f'Invalid Windows executable header: {path.name}')


def _extract_windows_ffmpeg(archive: Path, dest_dir: Path, *, archive_type: str) -> dict:
    """Extract only in disposable scratch directory; validate before publication."""
    scratch=dest_dir/'extracted'
    scratch.mkdir(parents=True,exist_ok=True)
    if archive_type=='7z':
        # Microsoft documents that modern Windows tar.exe handles .7z; no 7-Zip install.
        # Select native Windows tar.exe first; Git-bash GNU tar may not read 7z.
        native_tar=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'tar.exe'
        tar=str(native_tar) if native_tar.is_file() else shutil.which('tar')
        if not tar:
            raise RuntimeError('Windows tar.exe unavailable; switching to ZIP-compatible source')
        try:
            result=subprocess.run([tar,'-xf',str(archive),'-C',str(scratch)],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,errors='replace',timeout=240)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('7z extraction timed out') from exc
        if result.returncode!=0:
            raise RuntimeError('7z extraction failed: '+(result.stderr or '')[-400:])
    elif archive_type=='zip':
        with zipfile.ZipFile(archive) as z:
            selected={name: next((n for n in z.namelist() if n.lower().endswith('/bin/'+name)),None)
                      for name in ('ffmpeg.exe','ffprobe.exe')}
            if not all(selected.values()):
                raise RuntimeError('ZIP archive missing ffmpeg.exe / ffprobe.exe')
            for name,member in selected.items():
                with z.open(member) as source,(scratch/name).open('wb') as target:
                    shutil.copyfileobj(source,target,1024*1024)
    else:
        raise ValueError('Unsupported archive type')
    selected={}
    for exe in ('ffmpeg.exe','ffprobe.exe'):
        matches=[p for p in scratch.rglob(exe) if p.is_file() and 'bin' in p.parts]
        if not matches:matches=[p for p in scratch.rglob(exe) if p.is_file()]
        if len(matches)!=1:
            raise RuntimeError(f'Unexpected {exe} count in extracted files: {len(matches)}')
        _validate_ffmpeg_binary(matches[0]);selected[exe]=matches[0]
    return selected


def _validate_sha256(path: Path, expected: str) -> None:
    digest=None
    if hasattr(hashlib,'file_digest'):
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest is None:
        h=hashlib.sha256()
        with path.open('rb') as f:
            for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
        digest=h.hexdigest()
    if digest.lower()!=expected.lower():
        raise RuntimeError(f'FFmpeg archive SHA-256 mismatch ({digest[:16]}...)')


def install_ffmpeg_bundle():
    """Use existing tools before networking; prefer official 34MB 7z over 109MB ZIP."""
    ff=FFDIR/'ffmpeg.exe'; fp=FFDIR/'ffprobe.exe'
    if os.name != 'nt':
        return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
    if shutil.which('ffmpeg') and shutil.which('ffprobe'):
        print('[OK] FFmpeg and FFprobe found in system PATH; no download.',flush=True)
        return True
    copy_previous_asset('tools/ffmpeg/bin/ffmpeg.exe', ff, min_bytes=100_000)
    copy_previous_asset('tools/ffmpeg/bin/ffprobe.exe', fp, min_bytes=100_000)
    if not fp.exists():copy_previous_asset('tools/runtime/bin/ffprobe.exe',fp,min_bytes=100_000)
    if ff.exists() and fp.exists():
        try:
            _validate_ffmpeg_binary(ff);_validate_ffmpeg_binary(fp)
            print('[OK] Reused working FFmpeg / FFprobe from older installation or shared cache.',flush=True)
            cache_asset('tools/ffmpeg/bin/ffmpeg.exe',ff,min_bytes=100_000)
            cache_asset('tools/ffmpeg/bin/ffprobe.exe',fp,min_bytes=100_000)
            return True
        except (OSError,RuntimeError):pass
    FFDIR.mkdir(parents=True,exist_ok=True)
    print('[FFmpeg] Missing tools. First trying official 34 MB 7z (109 MB ZIP only as fallback).',flush=True)
    print('[FFmpeg] No manual extraction or 7-Zip installation is required.',flush=True)
    errors=[]
    for idx,(name,url,kind,expected_hash) in enumerate(FF_SOURCES,1):
        try:
            with tempfile.TemporaryDirectory(prefix='seedance_ffmpeg_') as tmp:
                work=Path(tmp)
                archive=work/('ffmpeg.'+kind)
                print(f'[FFmpeg] Source {idx}/{len(FF_SOURCES)}: {name}',flush=True)
                # Support a future larger offline bundle without modifying the installer:
                # only an archive whose published digest matches can be reused.
                local=ROOT/'installers'/('ffmpeg-9.0.2-essentials_build.'+kind)
                if local.is_file():
                    print('[FFmpeg] Verified local archive supplied with installer; network not required.',flush=True)
                    _validate_sha256(local,expected_hash)
                    archive=local
                else:
                    download_file(url,archive,label='FFmpeg',max_seconds=1200,
                                  connect_timeout=35,
                                  min_speed_mbps=0.08 if idx==1 else 0,
                                  slow_grace_seconds=50)
                    _validate_sha256(archive,expected_hash)
                print('[FFmpeg] SHA-256 verified. Extracting ...',flush=True)
                selected=_extract_windows_ffmpeg(archive,work,archive_type=kind)
                # Both binaries must be valid before either is made available.
                for exe,path in selected.items():
                    shutil.copy2(path,FFDIR/exe)
                _validate_ffmpeg_binary(ff);_validate_ffmpeg_binary(fp)
            cache_asset('tools/ffmpeg/bin/ffmpeg.exe',ff,min_bytes=100_000)
            cache_asset('tools/ffmpeg/bin/ffprobe.exe',fp,min_bytes=100_000)
            print('[OK] FFmpeg and FFprobe installed, with verified archive checksum.',flush=True)
            return True
        except (Exception,OSError) as exc:
            errors.append(f'{name}: {type(exc).__name__}: {str(exc)[:200]}')
            print(f'[WARN] FFmpeg source {idx} failed: {exc}',flush=True)
            if idx<len(FF_SOURCES):print('[FFmpeg] Trying the next official source automatically...',flush=True)
    print('[ERROR] All official FFmpeg sources failed. Already installed Python packages are retained.',flush=True)
    print('[ERROR] Try another network and rerun 01_INSTALL.cmd. No separate manual installers required.',flush=True)
    print('[ERROR] '+ ' | '.join(errors),flush=True)
    return False


def ensure_yolo_model():
    """Cache a small local YOLO model so object tracking does not depend on a later surprise download."""
    model_dir=TOOLS/'models';model_dir.mkdir(parents=True,exist_ok=True)
    target=model_dir/'yolo11n.pt'
    copy_previous_asset('tools/models/yolo11n.pt', target, min_bytes=1_000_000)
    if target.exists() and target.stat().st_size>1_000_000:
        cache_asset('tools/models/yolo11n.pt',target,min_bytes=1_000_000)
        print('[OK] YOLO 本地模型 yolo11n.pt 已存在。');return True
    if not module_ok('ultralytics'):
        print('[WARN] Ultralytics 未安装，暂不能准备 YOLO 本地模型。');return False
    code=(
        "from pathlib import Path; import os; from ultralytics import YOLO; "
        f"d=Path(r'{str(model_dir)}'); d.mkdir(parents=True,exist_ok=True); os.chdir(d); "
        "YOLO('yolo11n.pt'); p=d/'yolo11n.pt'; assert p.exists() and p.stat().st_size>1000000"
    )
    print('[INFO] 准备 YOLO 轻量本地模型（首次可能需要联网下载一次）...')
    ok=run([sys.executable,'-c',code],timeout=900)
    if ok: cache_asset('tools/models/yolo11n.pt',target,min_bytes=1_000_000)
    return ok

def install_tesseract():
    """Check, safely download, install and verify Tesseract without winget."""
    from tesseract_installer import install_tesseract as install_verified
    return install_verified()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['core','full'],default='full');args=ap.parse_args()
    print('='*72);print('Seedance Studio V3.9.8.4｜跨版本复用分析能力安装器');print('='*72)
    if studio_is_running():
        print('[需要关闭工作台] 当前本地服务仍在运行，不能安全替换 DLL / .pyd。')
        print('请先确保所有付费任务已结束，关闭工作台后再运行 01_INSTALL.cmd。')
        print('本次没有修改 Python 环境、历史任务或图片/视频素材。')
        return 11
    ok=True
    if os.name=='nt':ok=install_ffmpeg_bundle() and ok
    else:print('[INFO] 非 Windows 环境：保留现有 FFmpeg/FFprobe。')

    groups=[
        ('OpenCV（计算机视觉）',['opencv-python==5.0.0.93'], 'cv2'),
        ('PySceneDetect（镜头检测）',['scenedetect>=0.6.5,<0.7'], 'scenedetect'),
        ('Faster-Whisper（高速语音识别）',['faster-whisper>=1.1'], 'faster_whisper'),
    ]
    for label,specs,mod in groups:
        if module_ok(mod):print('[OK]',label,'已安装');continue
        print('[INFO] 安装',label,'...')
        if not install_pip(specs):
            print('[WARN]',label,'安装失败；工作台会自动降级，不阻断视频生成。');ok=False

    if args.mode=='full':
        if not install_mediapipe_safely():
            ok=False
        if not module_ok('ultralytics'):
            print('[INFO] 正在安装 YOLO / ByteTrack 主程序（保留已有 OpenCV）。')
            if not install_pip(['ultralytics==8.4.178']):
                print('[WARN] YOLO 组件安装失败，视觉追踪将自动降级。')
                ok=False
            elif not install_missing_dependencies('ultralytics'):
                print('[WARN] YOLO 部分辅助依赖未满足，可先使用其他视频分析功能。')
                ok=False
        if module_ok('ultralytics'):
            if not ensure_yolo_model():
                print('[WARN] YOLO 本地模型准备失败；目标追踪会自动降级，不阻断视频生成。');ok=False
        # WhisperX / PyTorch are sensitive to Python versions. Attempt only on a known compatible runtime.
        if sys.version_info[:2] <= (3,12):
            if not (shutil.which('whisperx') or module_ok('whisperx')):
                print('[INFO] 安装 WhisperX（精确语音对齐）...')
                if not install_pip(['whisperx>=3.3']):
                    print('[WARN] WhisperX 安装失败；将继续使用 Faster-Whisper 逐词时间戳。');ok=False
            else:print('[OK] WhisperX 已安装')
        else:
            print(f'[WARN] 当前 Python {sys.version.split()[0]}；WhisperX/PyTorch 兼容性可能不足，本次不强装。Faster-Whisper 继续提供逐词时间轴。')
            if not (shutil.which('whisperx') or module_ok('whisperx')):ok=False
        if not install_tesseract():ok=False

    print('\n[完成] 核心生成能力不会因为增强组件失败而停止。')
    print('重新打开“状态与设置 → 本地系统体检”即可看到最新状态。')
    return 0 if ok else 10

if __name__=='__main__':raise SystemExit(main())
