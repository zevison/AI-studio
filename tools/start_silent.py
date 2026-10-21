from __future__ import annotations
"""Direct Windows background starter (same app.py runtime as 04_DEBUG.cmd).

NO watchdog parent or polling supervisor is spawned. A single app.py subprocess
persists after the launcher exits; app.py handles in-flight task recovery.
"""
import ctypes
import ast
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
import traceback
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DATA.mkdir(parents=True, exist_ok=True)
LOG = DATA / 'startup.log'
def local_version():
    """One source of truth: read the APP_VERSION literal without importing providers."""
    tree=ast.parse((ROOT/'core'/'config.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='APP_VERSION' for t in node.targets):
            return str(ast.literal_eval(node.value))
    raise RuntimeError('找不到当前安装包版本号 APP_VERSION。')

EXPECTED_VERSION = local_version()
# IMPORTANT: local probes bypass proxy settings, including HTTP_PROXY/ALL_PROXY.
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HEALTH_PATH = '/api/bootstrap-health'
PORT_CANDIDATES = [8765] + list(range(8780, 8820)) + list(range(9000, 9030))

def log(message: str):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message + '\n')

def alert(message: str):
    log('ERROR ' + message)
    if os.name == 'nt':
        try:
            ctypes.windll.user32.MessageBoxW(0, message, 'Seedance Studio 启动失败', 0x10)
        except Exception:
            pass
    else:
        print(message, file=sys.stderr)

@contextmanager
def launch_lock():
    """Serialize concurrent double clicks across all launchers in this folder."""
    p = DATA / '.launcher.lock'
    with p.open('a+b') as f:
        f.seek(0)
        if f.tell()==0:
            f.write(b'1'); f.flush()
        end=time.monotonic()+115
        while True:
            try:
                f.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>end:
                    raise RuntimeError('其他启动操作尚未完成。请查看 data/startup.log。')
                time.sleep(.4)
        try:
            yield
        finally:
            f.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_UN)

def check_pid(pid):
    try:
        pid=int(pid)
        if pid<1:return False
        if os.name=='nt':
            k=ctypes.windll.kernel32
            k.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
            k.OpenProcess.restype=ctypes.c_void_p
            k.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
            k.GetExitCodeProcess.restype=ctypes.c_int
            k.CloseHandle.argtypes=[ctypes.c_void_p];k.CloseHandle.restype=ctypes.c_int
            h=k.OpenProcess(0x1000,0,pid)
            if not h:return False
            try:
                exit_code=ctypes.c_uint32()
                return bool(k.GetExitCodeProcess(h,ctypes.byref(exit_code))) and exit_code.value==259
            finally:k.CloseHandle(h)
        os.kill(pid,0);return True
    except Exception:return False

def settings_port():
    env=os.environ.get('PORT')
    if env:
        try:return int(env)
        except ValueError:pass
    p=ROOT/'.env'
    if p.exists():
        for raw in p.read_text(encoding='utf-8-sig',errors='ignore').splitlines():
            raw=raw.strip()
            if raw.startswith('PORT='):
                try:return int(raw.split('=',1)[1].strip().strip('"\''))
                except ValueError:pass
    return 8765

def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(.2)
        return sock.connect_ex(('127.0.0.1',port))==0

def health(port, timeout=1):
    try:
        with LOCAL_OPENER.open(f'http://127.0.0.1:{port}{HEALTH_PATH}',timeout=timeout) as resp:
            obj=json.load(resp)
            if obj.get('ok') and isinstance(obj.get('data'),dict):return obj['data']
    except Exception:
        pass
    return None

def same_root(info):
    if not info or not info.get('root_dir'):return False
    try:return Path(info['root_dir']).resolve()==ROOT.resolve()
    except Exception:return False

def own_running():
    ports=[]
    f=ROOT/'.runtime_port'
    if f.exists():
        try:ports.append(int(f.read_text(encoding='ascii').strip()))
        except Exception:pass
    ports.extend(PORT_CANDIDATES)
    for port in dict.fromkeys(ports):
        if not port_open(port):continue
        s=health(port)
        if same_root(s):return port,s
    return None,None

def pending():
    p=DATA/'.launch_pending.json'
    try:
        j=json.loads(p.read_text(encoding='utf-8'))
        if check_pid(j.get('pid')):
            return j
    except Exception:
        pass
    p.unlink(missing_ok=True)
    return None

def choose_port(preferred):
    for p in dict.fromkeys([preferred]+PORT_CANDIDATES):
        if not port_open(p):return p
    raise RuntimeError('未找到空闲本地端口；当前运行的旧版任务不会被强制结束。')

def background_flags():
    return getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0

def boot_stage():
    try:return (DATA/'startup_stage.txt').read_text(encoding='utf-8')[:180]
    except Exception:return '后台尚未报告启动阶段'

def verify_and_open(port, info):
    if not same_root(info):
        raise RuntimeError('端口响应的不是当前版本目录，已停止打开错误工作台。')
    if str(info.get('version'))!=EXPECTED_VERSION:
        raise RuntimeError(f'端口 {port} 返回 V{info.get("version")}，当前安装包为 V{EXPECTED_VERSION}，未混用旧版。')
    url=f'http://127.0.0.1:{port}/?v={EXPECTED_VERSION}'
    (ROOT/'.runtime_port').write_text(str(port),encoding='ascii')
    (DATA/'current_url.txt').write_text(url,encoding='utf-8')
    (DATA/'.launch_pending.json').unlink(missing_ok=True)
    log(f'READY version={EXPECTED_VERSION} port={port} pid={info.get("pid")} root={ROOT}')
    webbrowser.open_new(url)
    # Background-only read/check; no task cancellation or forced restart.
    updater = ROOT / 'tools' / 'update_client.py'
    if updater.is_file():
        try:
            subprocess.Popen([sys.executable, str(updater), '--check'],
                             cwd=str(ROOT), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=background_flags(), close_fds=True)
        except OSError as exc:
            log('Update check could not start: ' + type(exc).__name__)

def await_backend(port,seconds,process=None,pid=None):
    deadline=time.monotonic()+seconds
    last_note=0
    while time.monotonic()<deadline:
        h=health(port)
        if h:return h
        if process is not None and process.poll() is not None:
            raise RuntimeError(f'后台主程序已退出（退出码 {process.returncode}）。请检查 data/server_direct.log。')
        if pid is not None and not check_pid(pid):
            raise RuntimeError('原后台启动进程已退出。请检查 data/server_direct.log。')
        if time.monotonic()-last_note>=10:
            log(f'WAIT port={port} stage={boot_stage().strip()}')
            last_note=time.monotonic()
        time.sleep(.5)
    return None

def start_backend(port, env):
    target=ROOT/'app.py'
    if not target.is_file():raise RuntimeError('缺少后台主程序 app.py。')
    with (DATA/'server_direct.log').open('ab',buffering=0) as out:
        process=subprocess.Popen([sys.executable,'-u',str(target)],cwd=str(ROOT),env=env,
              stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
              creationflags=background_flags(),close_fds=True)
    log(f'Direct app.py spawned pid={process.pid} port={port} via {sys.executable}')
    return process


def supervise_backend(process,port,env,open_when_ready=False):
    """Protect browser refresh from a *crashed* server. Never re-submit paid tasks.

    If the backend is unresponsive but still alive, do NOT kill/restart it:
    doing so could interrupt an ongoing paid create request.
    """
    marker=ROOT/'.watchdog_stop'
    guard_pid=DATA/'.guardian_pid'
    guard_pid.write_text(str(os.getpid()),encoding='ascii')
    failures=[]
    try:
        while True:
            while process.poll() is None:
                if open_when_ready:
                    ready=health(port,timeout=.8)
                    if ready and same_root(ready) and str(ready.get('version'))==EXPECTED_VERSION:
                        try:
                            verify_and_open(port,ready)
                            open_when_ready=False
                        except Exception as exc:
                            log(f'Late-ready open failed: {exc}')
                time.sleep(2 if open_when_ready else 1)
            rc=process.returncode
            if marker.exists():
                log(f'Backend stopped by user, exit={rc}. Guardian shutting down.')
                break
            now=time.monotonic()
            failures=[x for x in failures if now-x<180]
            failures.append(now)
            log(f'Backend unexpectedly exited (code={rc}), crash_count_180s={len(failures)}; output=data/server_direct.log')
            if len(failures)>=4:
                log('Too many crashes in 180s; stopped auto-recovery. Check data/server_direct.log.')
                break
            time.sleep(3)
            if marker.exists():break
            # Never launch a second paid-task server on a port that belongs to someone else.
            if port_open(port):
                log(f'Port {port} unexpectedly in use. Refusing to launch duplicate backend.')
                break
            process=start_backend(port,env)
            (DATA/'.launch_pending.json').write_text(json.dumps({
                'pid':process.pid,'port':port,'created_at':time.time()
            }),encoding='utf-8')
            try:
                info=await_backend(port,90,process=process)
                if info and same_root(info) and str(info.get('version'))==EXPECTED_VERSION:
                    (DATA/'.launch_pending.json').unlink(missing_ok=True)
                    log(f'Backend recovered on port {port}, pid={process.pid}, version={EXPECTED_VERSION}')
                elif not info:
                    log(f'Recovery did not reach readiness after 90s; stage={boot_stage()}')
                else:
                    log(f'Recovery saw a foreign or mismatched backend on port {port}; stopping recovery')
                    break
            except Exception as exc:
                log(f'Recovery check failed: {exc}')
    finally:
        try:
            if guard_pid.read_text(encoding='ascii').strip()==str(os.getpid()):
                guard_pid.unlink(missing_ok=True)
        except OSError:pass


def main():
    own_process=None; own_port=None; own_env=None; wait_for_late_ready=False
    with launch_lock():
        log(f'Start Seedance V{EXPECTED_VERSION} from {ROOT} (Python {sys.version.split()[0]})')
        port,info=own_running()
        if info:
            log(f'Reuse existing app.py pid={info.get("pid")} on {port}')
            verify_and_open(port,info)
            return
        prior=pending()
        if prior:
            port=int(prior['port'])
            log(f'Existing app.py still starting pid={prior["pid"]}, port={port}; no duplicate process')
            info=await_backend(port,90,pid=prior['pid'])
            if info:
                verify_and_open(port,info);return
            raise RuntimeError('已有后台程序仍在启动，未再次启动以防任务重复。\n'+boot_stage())
        updater = ROOT / 'tools' / 'update_client.py'
        if updater.is_file():
            try:
                update = subprocess.run([sys.executable, str(updater), '--apply-ready'],
                                        cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=25, creationflags=background_flags())
                if update.returncode == 10:
                    log('Signed queued update started; current launcher exiting before install')
                    return
            except (OSError, subprocess.TimeoutExpired) as exc:
                log('Deferred update launch: ' + type(exc).__name__)
        port=choose_port(settings_port())
        env=os.environ.copy()
        env['PORT']=str(port)
        env['OPEN_BROWSER']='0'
        (ROOT/'.watchdog_stop').unlink(missing_ok=True)
        own_process=start_backend(port,env)
        own_port,own_env=port,env
        (DATA/'.launch_pending.json').write_text(json.dumps({
            'pid':own_process.pid,'port':port,'created_at':time.time()
        }),encoding='utf-8')
        info=await_backend(port,90,process=own_process)
        if info:
            verify_and_open(port,info)
        else:
            # A slow, still-running backend is NOT a failed process. Continue
            # watching for readiness instead of abandoning the only guardian.
            wait_for_late_ready=True
            log('Backend still alive after 90s; continuing startup without duplicate process. stage='+boot_stage())
            alert('后台仍在加载，已超过90秒；程序会继续等待并在就绪后自动打开网页。\n'
                  '当前启动阶段：'+boot_stage()+'\n如长时间没有打开，请发送 data/startup.log 和 data/server_direct.log。')
    if own_process is not None:
        supervise_backend(own_process,own_port,own_env,open_when_ready=wait_for_late_ready)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        log(traceback.format_exc())
        alert(str(exc))
        raise SystemExit(1)
