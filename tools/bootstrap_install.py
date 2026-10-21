"""Seedance Studio: independent per-release environments, reusable download caches.

Never remove a user's earlier .venv automatically. No paid video/API requests.
"""
from __future__ import annotations

import importlib.metadata
import queue
import threading
import time
import uuid
import os
import shutil
import subprocess
import struct
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from shared_runtime import ROOT, supported, same_family, mark_family, copy_previous_asset, cache_asset
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

APP_VERSION = '5.2.0'
VENV = ROOT / '.venv'
VENV_PY = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
REQ = ROOT / 'requirements.txt'
CF = ROOT / 'tools' / 'cloudflared.exe'
CF_URL = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'

def show_install_stage(stage: int, title: str) -> None:
    """Single readable checklist; confirmed stages only, not pretend download %."""
    stage=max(0,min(7,int(stage)))
    full=stage*4
    bar='#'*full + '-'*(28-full)
    print('\n'+'='*66,flush=True)
    print(f'  SEEDANCE STUDIO  |  V{APP_VERSION}  |  INSTALLATION',flush=True)
    print(f'  Completed checks: [{bar}] {stage} / 7',flush=True)
    print('  Now: '+title,flush=True)
    if stage<7:
        print('  Network downloads display ACTUAL MB below; remaining time varies.',flush=True)
    print('='*66+'\n',flush=True)



def run(cmd, timeout=2400):
    """Stream child process progress to BOTH console and dependency_install.log."""
    print('>', ' '.join(map(str, cmd)), flush=True)
    try:
        process=subprocess.Popen(list(map(str,cmd)), cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        output=queue.Queue(maxsize=128)
        stopped=threading.Event()
        def offer(value):
            while not stopped.is_set():
                try:output.put(value,timeout=.1);return
                except queue.Full:pass
        def read():
            try:
                for line in process.stdout:
                    if stopped.is_set():break
                    offer(line)
            finally:offer(None)
        reader=threading.Thread(target=read,daemon=True);reader.start()
        deadline=time.monotonic()+timeout;ended=False
        while not ended or process.poll() is None:
            if time.monotonic()>=deadline:
                stopped.set()
                # Stop only this installer child tree, never all Python tasks.
                if process.poll() is None:
                    try:
                        if os.name=='nt':
                            subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),'/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
                        else:process.kill()
                    finally:
                        if process.poll() is None:process.kill()
                process.wait(timeout=10)
                reader.join(timeout=2)
                if not reader.is_alive():process.stdout.close()
                print('[ERROR] Installation step timed out; current files retained. Rerun 01_INSTALL.cmd after checking the network.',flush=True)
                return 124
            if ended:time.sleep(.05);continue
            try:
                line=output.get(timeout=min(.2,max(.01,deadline-time.monotonic())))
                if line is None:ended=True
                else:print(line,end='',flush=True)
            except queue.Empty:pass
        reader.join(timeout=1)
        if not reader.is_alive():process.stdout.close()
        return process.wait(timeout=10)
    except (OSError, ValueError) as exc:
        print('[ERROR] Child process start failed:', exc)
        return 127


def _backup_broken_link():
    """Rename instead of destroying user's installed libraries/data."""
    if not os.path.lexists(VENV):
        return
    target = ROOT / ('.venv_saved_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    VENV.rename(target)
    print(f'[保留] 不兼容或损坏的运行环境已改名为 {target.name}；没有删除。')


def prepare_venv() -> str:
    # A release must remain runnable after a colleague deletes an older release.
    # Do not install into a shared junction: pip can replace DLLs used by another
    # running release. Local venvs also work on drives without NTFS junctions.
    linked = VENV.is_symlink() or (hasattr(VENV, 'is_junction') and VENV.is_junction())
    if not linked and supported(VENV) and same_family(VENV):
        return '当前目录已有兼容运行环境，直接复用'
    if os.path.lexists(VENV):
        _backup_broken_link()
    print(f'[首次] 创建当前版本独立 Python 环境：{VENV}；不依赖旧版本目录。')
    if run([sys.executable, '-m', 'venv', str(VENV)]) != 0 or not supported(VENV):
        raise RuntimeError('独立 Python 环境创建失败；请检查磁盘空间、目录写权限及安全软件日志。现有环境已保留。')
    mark_family(VENV)
    return '已创建当前版本独立 Python 环境；pip 下载缓存仍可复用'


def missing_requirements() -> list[str]:
    """Ask installed interpreter to check actual package distributions and versions."""
    import json
    lines=[line.split(' #',1)[0].strip() for line in REQ.read_text(encoding='utf-8').splitlines()
           if line.strip() and not line.lstrip().startswith('#')]
    try:
        p=subprocess.run([str(VENV_PY),str(ROOT/'tools/check_requirements.py'),str(REQ)],
                         cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40)
        if p.returncode == 0:
            return json.loads(p.stdout.strip().splitlines()[-1])
        print('[WARN] 核对依赖时无法读取完整信息：',p.stderr[-400:])
    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired) as exc:
        print('[WARN] 核对依赖失败：',exc)
    return lines


def ensure_requirements():
    missing = missing_requirements()
    if not missing:
        print('[OK] 核心 Python 库均已安装且版本满足要求，跳过 pip 下载。')
        return
    print('[缺失] 仅补装以下依赖：', ', '.join(missing))
    if run([str(VENV_PY),'-m','pip','install','--disable-pip-version-check','--prefer-binary',
        '--constraint',str(ROOT/'tools/analysis_constraints.txt'),*missing]) != 0:
        raise RuntimeError('缺失的核心库安装失败（已有依赖保留）')
    if missing_requirements():
        raise RuntimeError('核心依赖安装后仍未通过版本核验')


def install_complete_analysis() -> int:
    """Run the former 08/10/13 install stages every time 01 is used.

    Do not use the legacy one-shot marker: an interrupted/partial installation
    must be resumable. The analysis installer skips already working modules.
    """
    script = ROOT / 'tools' / 'install_analysis_stack.py'
    if not script.is_file():
        print('[ERROR] Complete video/voice installer is missing from the ZIP.')
        return 10
    print('[4/7] Installing/verifying ALL video, speech, vision, OCR components ...', flush=True)
    rc = run([str(VENV_PY), str(script), '--mode', 'full'])
    if rc == 11:
        raise RuntimeError('Studio is running. Stop it before installing native video dependencies.')
    if rc:
        print('[WARN] Some advanced components remain unavailable. Installation details are in the output.')
    return rc


def install_original_post_downloader() -> bool:
    """Install the former 14 stage with version-aware checks, no paid provider calls."""
    print('[5/7] Checking yt-dlp and curl_cffi for Bright Data original-post review ...', flush=True)
    code = ('import importlib.util,sys;'
            'sys.exit(0 if all(importlib.util.find_spec(x) for x in ("yt_dlp","curl_cffi")) else 1)')
    check = run([str(VENV_PY), '-c', code])
    if check != 0:
        if run([str(VENV_PY), '-m', 'pip', 'install', '--disable-pip-version-check',
                '--prefer-binary','--constraint',str(ROOT/'tools/analysis_constraints.txt'), 'yt-dlp[default,curl-cffi]']) != 0:
            print('[WARN] Downloader dependencies could not be installed. No account or service was accessed.')
            return False
    # Test using the exact Studio interpreter, including its local
    # impersonation targets and FFprobe; no TikTok request or paid API call.
    probe=ROOT/'tools'/'check_bright_downloader.py'
    if probe.exists():
        if run([str(VENV_PY), str(probe)]) != 0:
            print('[WARN] Downloader libraries exist but full local capability test failed.')
            return False
    return run([str(VENV_PY), '-c', code]) == 0


def probe_scenedetect() -> tuple[bool, str]:
    """Check the real editor API in the exact Studio interpreter, not system Python."""
    script = ('from scenedetect import open_video, SceneManager; '
              'from scenedetect.detectors import ContentDetector; '
              'assert callable(open_video) and callable(ContentDetector)')
    try:
        p = subprocess.run([str(VENV_PY), '-c', script], cwd=ROOT,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if p.returncode:
        return False, (p.stderr or p.stdout or 'Unknown import error')[-1500:]
    return True, ''


def repair_scenedetect() -> bool:
    """One-time automatic repair of tiny pure-Python PySceneDetect wheel.

    Avoid replacing OpenCV/Numpy on the first attempt. If a missing dependency
    prevents import, retry with package-managed dependencies only once. All
    attempts go through the existing console+log stream, no user shell steps.
    """
    ok, reason = probe_scenedetect()
    if ok:
        print('[OK] PySceneDetect imports and editor APIs verified.', flush=True)
        return True
    print('[REPAIR] PySceneDetect unavailable; automatic repair starts.', flush=True)
    print('[REPAIR] First diagnostic: ' + reason[-700:].replace('\n', ' | '), flush=True)
    spec = 'scenedetect>=0.6.5,<0.7'
    # pip --no-deps leaves verified native OpenCV wheels and NumPy untouched.
    pip = [str(VENV_PY), '-m', 'pip', 'install', '--disable-pip-version-check',
           '--prefer-binary', '--only-binary=:all:','--constraint',str(ROOT/'tools/analysis_constraints.txt')]
    first = [*pip, '--force-reinstall', '--no-deps', spec]
    if run(first) == 0:
        ok, reason = probe_scenedetect()
        if ok:
            print('[OK] PySceneDetect repaired without changing native dependencies.', flush=True)
            return True
    # Missing helper libraries can cause an import failure even when the wheel
    # is installed. Standard dependency resolution is an explicit last resort.
    print('[REPAIR] Retrying PySceneDetect with declared dependencies.', flush=True)
    run([*pip, spec])
    ok, reason = probe_scenedetect()
    if ok:
        print('[OK] PySceneDetect repaired and verified.', flush=True)
        return True
    print('[ERROR] PySceneDetect still fails import: ' + reason[-1000:].replace('\n', ' | '), flush=True)
    return False


def capability_report(*, full_analysis_rc: int, downloader_ok: bool, ffmpeg_ok: bool) -> bool:
    """Persist verifiable installation status. Missing essential modules are not hidden."""
    import json
    import importlib.util
    from datetime import datetime, timezone
    logdir = ROOT/'install_logs'
    logdir.mkdir(exist_ok=True)
    check_modules = {
        'opencv':'cv2', 'scenedetect':'scenedetect',
        'faster_whisper':'faster_whisper', 'yt_dlp':'yt_dlp',
        'curl_cffi':'curl_cffi', 'whisperx':'whisperx',
        'ultralytics':'ultralytics', 'mediapipe':'mediapipe',
    }
    # Check availability from the ACTUAL Studio Python, not bootstrap interpreter.
    probe = ('import json,importlib.util,sys;'
             f'print(json.dumps({{k:(importlib.util.find_spec(v) is not None) for k,v in {check_modules!r}.items()}}))')
    try:
        p = subprocess.run([str(VENV_PY),'-c',probe],capture_output=True,text=True,
                           encoding='utf-8',errors='replace',timeout=35,cwd=ROOT)
        modules = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode==0 else {}
    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):
        modules={}
    required = ('opencv','scenedetect','faster_whisper','yt_dlp','curl_cffi')
    # Distribution presence alone is insufficient; validate imports using the
    # Studio interpreter, so missing DLLs / broken native wheels cannot pass.
    actual_imports={}
    import_errors={}
    for key in (*required,'whisperx','ultralytics'):
        print(f'[CHECK] Importing {key} in the installed runtime ...',flush=True)
        if not modules.get(key):
            actual_imports[key]=False
            import_errors[key]='Module not found in Studio Python (importlib.find_spec).'
            continue
        if key=='scenedetect':
            success,err=probe_scenedetect()
            actual_imports[key]=success
            if success:print('[OK] scenedetect APIs imported.',flush=True)
            if not success:
                import_errors[key]=err[-1200:]
                print('[WARN] scenedetect API import check failed: '+err[-500:].replace('\n',' | '))
            continue
        try:
            statement=('from whisperx.asr import load_model; from whisperx.alignment import align'
                       if key=='whisperx' else f'import {check_modules[key]}')
            probe_result=subprocess.run([str(VENV_PY),'-c',statement],
                                        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                        encoding='utf-8',errors='replace',timeout=90 if key=='whisperx' else 45,cwd=ROOT)
            actual_imports[key]=probe_result.returncode==0
            if actual_imports[key]:print(f'[OK] {key} imported.',flush=True)
            if not actual_imports[key]:
                import_errors[key]=(probe_result.stderr or 'Nonzero exit')[-1200:]
                print(f'[WARN] {key} is installed but fails to import in the current Python runtime: '+import_errors[key][-400:].replace('\n',' | '))
        except (OSError,subprocess.TimeoutExpired) as exc:
            actual_imports[key]=False
            import_errors[key]=str(exc)
    required_missing=[x for x in required if not actual_imports.get(x)]
    if not ffmpeg_ok: required_missing.append('ffmpeg / ffprobe')
    if not downloader_ok and 'yt_dlp' not in required_missing:
        required_missing.append('TikTok downloader check (see downloader diagnostics; FFprobe may be the shared cause)')
    pose_marker=Path(os.environ.get('LOCALAPPDATA',''))/'SeedanceStudio/runtime/pose111/pose-verified.json'
    modules['mediapipe']=pose_marker.is_file() or modules.get('mediapipe',False)
    optional_missing=[x for x in ('whisperx','ultralytics','mediapipe') if not modules.get(x)]
    optional_missing += [x for x in ('whisperx','ultralytics') if modules.get(x) and not actual_imports.get(x)]
    print('[CHECK] Validating complete Python dependency consistency ...',flush=True)
    consistency=subprocess.run([str(VENV_PY),'-m','pip','check'],capture_output=True,
        encoding='utf8',errors='replace',timeout=45,cwd=ROOT)
    if consistency.returncode:required_missing.append('Python dependency consistency (pip check)')
    try:
        from core.tesseract_support import check_tesseract
        tess=bool(check_tesseract().get('available'))
    except Exception:
        tess=False
    if not tess:optional_missing.append('tesseract OCR')
    report={
        'version':APP_VERSION,
        'recorded_utc':datetime.now(timezone.utc).isoformat(),
        'status':'COMPLETE' if not required_missing and not optional_missing else ('INCOMPLETE' if required_missing else 'ENHANCED_PARTIAL'),
        'required_missing':required_missing,
        'enhanced_missing':optional_missing,
        'analysis_installer_exit_code':full_analysis_rc,
        'modules':modules,
        'required_imports':actual_imports,
        'required_import_errors':import_errors,
        'ffmpeg_probe_ok':ffmpeg_ok,
        'downloader_installed':downloader_ok,
        'pip_check_passed':consistency.returncode==0,
        'pip_check_diagnostics':consistency.stdout[-4000:],
        'note':'No network/provider success claim; capability check only. Some optional modules can be unsupported on Python 3.12.',
    }
    path=logdir/'dependency_report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('[INSTALL RESULT]',report['status'])
    if required_missing:print('[ERROR] Missing essential components:',', '.join(required_missing))
    if optional_missing:print('[WARN] Advanced components not confirmed:',', '.join(optional_missing))
    print('[REPORT]',path)
    return not required_missing


def prepare_cloudflared():
    if copy_previous_asset('tools/cloudflared.exe', CF, min_bytes=1_000_000):
        cache_asset('tools/cloudflared.exe',CF,min_bytes=1_000_000)
        return
    if os.environ.get('SEEDANCE_INSTALL_CLOUDFLARED')!='1':
        print('[可选] Cloudflare 调试隧道未启用，跳过下载；OSS 主通道不需要它。')
        return
    print('[可选] 按显式配置准备 cloudflared；OSS 模式不需要此组件。')
    try:
        req=urllib.request.Request(CF_URL,headers={'User-Agent':'SeedanceStudio/'+APP_VERSION})
        tmp=CF.with_suffix('.download')
        from trusted_download import open_public_url
        with open_public_url(req,timeout=40) as r, open(tmp,'wb') as f:
            shutil.copyfileobj(r,f)
        if tmp.stat().st_size < 1_000_000:
            raise RuntimeError('文件长度异常')
        tmp.replace(CF)
        cache_asset('tools/cloudflared.exe',CF,min_bytes=1_000_000)
        print('[OK] Cloudflare 调试隧道已准备完成')
    except Exception as exc:
        CF.with_suffix('.download').unlink(missing_ok=True)
        print('[WARN] cloudflared 下载未完成，OSS 主通道不受影响：',exc)


def host_supported():
    """Compatibility lane for the legacy script installer, not the EXE host."""
    return sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8


def main() -> int:
    print('='*70)
    print(f'Seedance Studio V{APP_VERSION} | 时区自检 + 跨版本复用')
    print('='*70)
    if not host_supported():
        print('[ERROR] 完整安装包需要 Python 3.12 x64。请使用 01_INSTALL.cmd 自动选择或安装匹配版本。')
        return 2
    if os.name != 'nt':
        print('[WARN] 发行版目标为 Windows；当前环境仅适合测试安装逻辑。')
    env_file=ROOT/'.env'
    if not env_file.exists() and (ROOT/'.env.example').is_file():
        shutil.copy2(ROOT/'.env.example',env_file)
    for rel in ('data','data/assets','data/outputs','data/cache','tools'):
        (ROOT/rel).mkdir(parents=True,exist_ok=True)
    try:
        show_install_stage(0,'Creating or finding the isolated Python runtime')
        from install_analysis_stack import studio_is_running, install_ffmpeg_bundle
        if studio_is_running():
            raise RuntimeError('Studio is still running. Please close it and rerun 01_INSTALL.cmd.')
        print('[1/7] ' + prepare_venv())
        show_install_stage(1,'Python runtime ready. Installing core packages')
        print('[2/7] Checking core Python packages ...')
        ensure_requirements()
        # Check an actual import in the selected virtual environment BEFORE startup.
        # Uses bundled IANA TZif on Windows if tzdata was never installed.
        if run([str(VENV_PY), str(ROOT / 'tools' / 'check_timezone.py')]) != 0:
            raise RuntimeError('美国市场时区自检失败。请确认 assets/timezones/America_New_York.tzif 未被删除。')
        # Always run complete installation; no environment flag, no separate 08/10.
        show_install_stage(2,'Core packages verified. Checking FFmpeg / FFprobe')
        print('[3/7] FFmpeg + FFprobe | verify and download (progress shown in MB)...',flush=True)
        ffmpeg_ok = install_ffmpeg_bundle() if os.name=='nt' else True
        show_install_stage(3,'FFmpeg stage completed. Checking vision / speech / OCR')
        analysis_rc = install_complete_analysis()
        show_install_stage(4,'Vision and speech installation attempt completed. Checking downloader')
        downloader_ok = install_original_post_downloader()
        show_install_stage(5,'Downloader check finished. Preparing optional Cloudflare helper')
        print('[6/7] Preparing Cloudflare helper and preserving current configuration ...')
        prepare_cloudflared()
        # Run only after optional packages have finished changing the environment.
        # Do not fail a fresh PC setup on a simple missing pure-Python wheel.
        repair_scenedetect()
        show_install_stage(6,'Preparing final capability report and dependency validation')
        print('[7/7] Checking installed components and saving report ...')
        essential_ok = capability_report(full_analysis_rc=analysis_rc,
                                         downloader_ok=downloader_ok,
                                         ffmpeg_ok=ffmpeg_ok)
        if not essential_ok:
            print('[ERROR] Core review dependencies incomplete. You may rerun 01_INSTALL.cmd safely.')
            return 10
    except Exception as exc:
        print('[ERROR]',exc)
        return 3
    show_install_stage(7,'All 7 setup stages passed the required component checks')
    print('\n[OK] 01 dependency installation finished. See install_logs/dependency_report.json for any advanced features unavailable.')
    print('当前版本使用独立 .venv。移动安装目录或更换电脑后，请重新运行 01_INSTALL.cmd。')
    return 0


if __name__=='__main__':
    # Persist complete Python and subprocess output; help diagnose a stalled or
    # incomplete first install without any extra installer/manual commands.
    import contextlib
    class ConsoleLog:
        def __init__(self, primary, log):self.primary,self.log=primary,log
        def write(self, data):
            self.primary.write(data)
            self.log.write(data)
        def flush(self):
            self.primary.flush()
            self.log.flush()
    folder=ROOT/'install_logs'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'dependency_install.log').open('a',encoding='utf-8',errors='replace',buffering=1) as logfile:
        with contextlib.redirect_stdout(ConsoleLog(sys.stdout,logfile)), contextlib.redirect_stderr(ConsoleLog(sys.stderr,logfile)):
            print('\n========== INSTALL RUN '+datetime.now().isoformat()+' ==========')
            raise SystemExit(main())
