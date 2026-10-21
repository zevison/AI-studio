"""Frozen-safe desktop entry point. No account calls are made by this host."""
from __future__ import annotations

import argparse
import ctypes
from contextlib import ExitStack,contextmanager
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

WORKER_MODULES = frozenset({'yt_dlp'})
ENTRY_STARTED=time.perf_counter()
if getattr(sys,'frozen',False):
    # Resource directories remain immutable even when a helper is loaded from
    # its packaged .py file instead of the embedded archive.
    sys.dont_write_bytecode=True


def restore_standard_streams():
    """Windowed PyInstaller sets streams to None, including worker pipe mode."""
    for name, number in (('stdout',-11),('stderr',-12)):
        existing=getattr(sys,name)
        if existing is not None:
            # Redirected streams can still use the Windows ACP (for example
            # GBK). Parent supervisors and diagnostic readers consume UTF-8.
            # Limit this process-wide change to our frozen host, not callers
            # importing the launcher in a development/test interpreter.
            if getattr(sys,'frozen',False) and callable(getattr(existing,'reconfigure',None)):
                existing.reconfigure(encoding='utf8',errors='backslashreplace',line_buffering=True)
            continue
        stream=None
        if os.name=='nt':
            import msvcrt
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetStdHandle.argtypes=[wintypes.DWORD];kernel.GetStdHandle.restype=wintypes.HANDLE
            kernel.GetCurrentProcess.restype=wintypes.HANDLE
            kernel.DuplicateHandle.argtypes=[wintypes.HANDLE,wintypes.HANDLE,wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE),wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            handle=kernel.GetStdHandle(number&0xffffffff);duplicate=wintypes.HANDLE()
            current=kernel.GetCurrentProcess()
            if handle and handle!=wintypes.HANDLE(-1).value and kernel.DuplicateHandle(current,handle,current,ctypes.byref(duplicate),0,False,2):
                fd=msvcrt.open_osfhandle(duplicate.value,os.O_WRONLY|os.O_BINARY)
                stream=io.TextIOWrapper(os.fdopen(fd,'wb'),encoding='utf8',errors='replace',line_buffering=True)
        setattr(sys,name,stream or open(os.devnull,'w',encoding='utf8'))
    if sys.stdin is None:sys.stdin=open(os.devnull,'r',encoding='utf8')


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def prepare_environment(home=None):
    """Must run before importing any app/core module or touching credentials."""
    target = Path(home or os.getenv('STUDIO_DATA_HOME') or
                  Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'SeedanceStudio').resolve()
    target.mkdir(parents=True, exist_ok=True)
    for key, value in {'STUDIO_DATA_HOME':str(target), 'STUDIO_PROFILE_HOME':str(target),
                       'HOST':'127.0.0.1', 'PORT':'0', 'OPEN_BROWSER':'0',
                       'STUDIO_WORKSPACE_ID':'owner', 'STUDIO_WORKSPACE_TOKEN':'',
                       'STUDIO_CONTROL_DB':str(target/'data/team-control.sqlite'),
                       'STUDIO_DESKTOP':'1'}.items():
        os.environ[key] = value
    # Never inherit a development machine's API keys into the installed app.
    # Existing same-user DPAPI credentials remain available through profile_store.
    for key in ('OPENLUX_API_KEY','OPENLUX_ACCOUNT_ACCESS_TOKEN','OPENLUX_ACCOUNT_USER_ID',
                'BRIGHTDATA_API_KEY','OSS_ACCESS_KEY_ID','OSS_ACCESS_KEY_SECRET',
                'R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','HF_TOKEN','HUGGING_FACE_HUB_TOKEN',
                'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS','WEBVIEW2_BROWSER_EXECUTABLE_FOLDER',
                'WEBVIEW2_USER_DATA_FOLDER'):
        os.environ.pop(key, None)
    if getattr(sys,'frozen',False):
        for key in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX','NODE_PATH','NODE_OPTIONS',
                    'PYTHONNET_RUNTIME','PYTHONNET_CORECLR_RUNTIME_CONFIG','PYWEBVIEW_GUI','IMAGEIO_FFMPEG_EXE'):
            os.environ.pop(key,None)
        system=Path(os.environ.get('SystemRoot',r'C:\Windows'))
        root=resource_root()
        os.environ['PATH']=os.pathsep.join(map(str,(root/'tools/ffmpeg/bin',root/'tools/node',root/'tools/tesseract',
            system/'System32',system,system/'System32/WindowsPowerShell/v1.0',system/'System32/Wbem')))
    return target


def maybe_migrate(home, source=None, prompt=False):
    """Only explicit user selection authorizes importing an old data directory."""
    if (Path(home)/'data').exists():
        if source:raise FileExistsError('目标已有数据，已拒绝覆盖。请先在设置中备份。')
        return None
    if not source and prompt:
        answer=ctypes.windll.user32.MessageBoxW(None,
            '是否从以前的 Seedance Studio 安装目录迁移历史和素材？\n\n选择“是”后选择旧安装目录；选择“否”开始全新使用。\n原目录不会被删除或修改。',
            'Seedance Studio 首次启动',0x24)
        if answer==6:
            import clr
            clr.AddReference('System.Windows.Forms')
            from System.Windows.Forms import FolderBrowserDialog,DialogResult
            from System.Threading import Thread,ThreadStart,ApartmentState
            selected=[];errors=[]
            def choose_folder():
                dialog=FolderBrowserDialog();dialog.Description='选择包含 data 文件夹的旧安装目录'
                dialog.ShowNewFolderButton=False
                try:
                    if dialog.ShowDialog()==DialogResult.OK:selected.append(Path(str(dialog.SelectedPath)))
                except Exception as error:errors.append(error)
                finally:dialog.Dispose()
            chooser=Thread(ThreadStart(choose_folder));chooser.SetApartmentState(ApartmentState.STA)
            chooser.Start();chooser.Join()
            if errors:raise errors[0]
            if selected:source=selected[0]
    if source:
        from core.desktop_migration import migrate_legacy
        return migrate_legacy(Path(source),Path(home))
    return None


def atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


class InstanceLock:
    """Windows named mutex: process exit releases ownership even after a crash."""
    def __init__(self, home):
        self.name='Local\\SeedanceStudio-'+hashlib.sha256(str(Path(home).resolve()).casefold().encode()).hexdigest()[:24]
        self.handle=None

    def __enter__(self):
        if os.name!='nt':
            raise RuntimeError('This desktop application requires Windows 10/11 x64')
        kernel=ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        kernel.CreateMutexW.restype=wintypes.HANDLE
        self.handle=kernel.CreateMutexW(None, False, self.name)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle(self.handle);self.handle=None
            raise RuntimeError('Seedance Studio 已在当前用户目录运行，请先关闭已有窗口。')
        return self

    def __exit__(self,*_):
        if self.handle:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle(self.handle);self.handle=None


class ChildJob:
    """Only owns the new backend process and descendants; never production PIDs."""
    def __init__(self, process=None):
        self.handle=None
        if os.name!='nt':return
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                      ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),
                      ('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),
                      ('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount',
                'OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
                      ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.CreateJobObjectW(None,None)
        # Only the authenticated update-helper launch explicitly asks to break
        # away; normal backend/Node/Chromium children remain owned by this job.
        limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000|0x0800
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or (process is not None and not kernel.AssignProcessToJobObject(handle,wintypes.HANDLE(int(process._handle)))):
            error=ctypes.get_last_error();kernel.CloseHandle(handle);raise ctypes.WinError(error)
        self.handle=handle

    def add_process(self,handle):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        if not self.handle or not kernel.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle(self.handle);self.handle=None


class LoadingNotice:
    """Small native progress window while first-use runtimes are verified."""
    def __init__(self):
        self.message='正在准备桌面环境；首次启动需要验证本地组件。'
        self.stop=threading.Event();self.thread=None

    def start(self):
        def show():
            try:
                import clr
                clr.AddReference('System.Windows.Forms');clr.AddReference('System.Drawing')
                from System.Windows.Forms import Form,Label,Application,FormStartPosition,FormBorderStyle
                from System.Drawing import Size,Point,Icon
                form=Form();form.Text='Seedance Studio · 启动中';form.Size=Size(500,150)
                icon=resource_root()/'assets/branding/seedance-studio.ico'
                if icon.is_file():form.Icon=Icon(str(icon))
                form.StartPosition=FormStartPosition.CenterScreen;form.FormBorderStyle=FormBorderStyle.FixedDialog
                form.ControlBox=False
                label=Label();label.Location=Point(18,24);label.Size=Size(450,68);form.Controls.Add(label)
                form.Show()
                while not self.stop.wait(.1):
                    label.Text=self.message;Application.DoEvents()
                form.Close();form.Dispose()
            except Exception:
                # The normal desktop startup will report a CLR failure explicitly.
                pass
        self.thread=threading.Thread(target=show,name='desktop-loading-notice',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=3)


class StartupTrace:
    """Non-sensitive, per-session timings; diagnostic IO never blocks startup."""
    def __init__(self,path):
        self.path=path;self.started=time.perf_counter();self.events=[];self.lock=threading.RLock()

    def add(self,stage,seconds=0,status='PASS'):
        with self.lock:
            self.events.append({'stage':stage,'seconds':round(seconds,4),
                'since_trace_start':round(time.perf_counter()-self.started,4),'status':status})
            try:atomic_json(self.path,{'format':'seedance.startup-timing@1','pid':os.getpid(),'events':self.events})
            except OSError:pass

    @contextmanager
    def measure(self,stage):
        started=time.perf_counter()
        try:yield
        except BaseException:
            self.add(stage,time.perf_counter()-started,'FAIL');raise
        else:self.add(stage,time.perf_counter()-started)


class WebViewPreparation:
    """Independent validation overlaps backend work; GUI waits for both gates."""
    def __init__(self,trace,allow_evergreen=False):
        self.done=threading.Event();self.value=None;self.error=None
        def run():
            try:
                with trace.measure('webview_integrity_and_acl'):
                    self.value=ensure_webview(allow_evergreen=allow_evergreen)
            except BaseException as error:self.error=error
            finally:self.done.set()
        self.thread=threading.Thread(target=run,name='desktop-webview-validation',daemon=True)
        self.thread.start()

    def result(self,timeout):
        if not self.done.wait(max(0,timeout)):
            raise TimeoutError('WebView2 本地组件验证超时，请保留启动日志并重新安装；用户数据未删除。')
        if self.error is not None:raise self.error
        return self.value


def validate_gui_page(page,url):
    if (not isinstance(page,dict) or page.get('origin')!=url.rstrip('/')
        or page.get('application')!='seedance-studio' or page.get('ready') not in ('interactive','complete')
        or not str(page.get('title','')).startswith('Seedance Studio')):
        raise RuntimeError('本地网页未通过应用身份检查，已停止启动，避免把错误页面当作就绪。')
    return page


def guard_gui_readiness(ready,closing,timeout,request_close,force_exit,grace=8):
    """Bound the GUI gate even if its load callback never runs."""
    deadline=time.monotonic()+timeout
    while not closing.is_set():
        if ready.wait(min(.2,max(0,deadline-time.monotonic()))):return 'ready'
        if time.monotonic()>=deadline:break
    if closing.is_set():return 'closed'
    if request_close() is False:return 'ready' if ready.is_set() else 'closed'
    if not closing.wait(grace):force_exit()
    return 'timeout'


def verify_managed_analysis_components():
    root=resource_root()
    for relative,filename,format_name in (
        ('tools/node','runtime-manifest.json','seedance.node-managed@1'),
        ('tools/ffmpeg','runtime-manifest.json','seedance.ffmpeg-managed@1'),
        ('tools/tesseract','runtime-manifest.json','seedance.tesseract-portable@1'),
        ('tools/runtime/models/faster-whisper-small','model-manifest.json','seedance.faster-whisper-model@1')):
        folder=root/relative;manifest=folder/filename
        if not manifest.is_file():
            if relative=='tools/runtime/models/faster-whisper-small':
                from core.component_manager import catalog
                if 'asr' in catalog(root):continue
            if getattr(sys,'frozen',False):raise RuntimeError('Packaged OCR/ASR component manifest is missing; repair installation')
            continue
        document=json.loads(manifest.read_text('utf8'))
        if document.get('format')!=format_name or not document.get('files'):
            raise RuntimeError('Managed analysis component manifest is invalid')
        files=document['files']
        rows=files.items() if isinstance(files,dict) else ((row['path'],row['sha256']) for row in files)
        for name,checksum in rows:
            path=folder/name
            if (not path.resolve().is_relative_to(folder.resolve()) or path.is_symlink()
                    or any(getattr(item,'is_junction',lambda:False)() for item in (path,*path.parents))
                    or not path.is_file() or file_digest(path)!=checksum):
                raise RuntimeError('Packaged OCR/ASR component is incomplete or changed; repair installation')


def install_local_runtimes(home):
    verify_managed_analysis_components()
    root=resource_root(); manifest=root/'tools/hypit_bundle.json'
    if not manifest.is_file():
        if getattr(sys,'frozen',False):raise RuntimeError('Packaged Hypit runtime manifest is missing')
        return
    sys.path.insert(0,str(root/'tools'))
    from install_hypit_bundle import extract_verified
    document=json.loads(manifest.read_text(encoding='utf8'))
    if document.get('format')!='seedance.hypit-bundle@1':raise RuntimeError('Unknown bundled runtime format')
    results={}
    for kind in ('sdk','browser'):
        item=document[kind]
        if kind=='sdk' and document.get('sdk_lazy') is True:
            if item.get('file')!='sdk-payload.zip' or not re.fullmatch('[a-f0-9]{64}',item.get('sha256','')):
                raise RuntimeError('Invalid lazy bundled SDK identity')
            results[kind]='bundled-lazy-before-use-verification'
            continue
        if kind=='browser' and item.get('deferred') is True:
            from core.component_manager import catalog
            if catalog(root).get('browser',{}).get('files',[{}])[0].get('sha256')!=item.get('sha256'):
                raise RuntimeError('Deferred browser catalog does not match bundled runtime identity')
            results[kind]='pending-background-component'
            continue
        if item.get('file')!=kind+'-payload.zip':raise RuntimeError('Invalid managed runtime file')
        archive=root/'tools'/item['file']
        destination=Path(home)/'assets/hypit-host'/(kind+'-'+item['sha256'][:12])
        try:results[kind]=extract_verified(archive,destination,item['sha256'])
        except RuntimeError:
            # An interrupted extraction or damaged managed cache is recoverable.
            # Preserve its bytes; never remove user data or trust a changed archive.
            if (not destination.exists() or destination.is_symlink()
                or (hasattr(destination,'is_junction') and destination.is_junction())
                or hashlib.sha256(archive.read_bytes()).hexdigest()!=item['sha256']):raise
            quarantine=destination.with_name(destination.name+'.damaged-'+secrets.token_hex(6))
            destination.rename(quarantine)
            extract_verified(archive,destination,item['sha256'])
            results[kind]='recovered'
            atomic_json(Path(home)/'runtime-recovery.json',{'component':kind,'preserved':str(quarantine),'status':'restored-from-verified-bundle'})
    return results


def backend(home, ready_file, shutdown_file):
    trace=StartupTrace(Path(ready_file).parent/'backend-timing.json')
    with trace.measure('local_runtime_integrity_and_extraction'):components=install_local_runtimes(home)
    for name,state in (components or {}).items():trace.add(name+'_'+state)
    with trace.measure('application_import'):
        import app
    stop=threading.Event()
    def watch():
        while not stop.wait(.3):
            if shutdown_file and Path(shutdown_file).exists():stop.set()
    threading.Thread(target=watch,name='desktop-stop-file',daemon=True).start()
    database_started=time.perf_counter()
    def ready(url):
        trace.add('database_and_server_initialization',time.perf_counter()-database_started)
        trace.add('backend_ready')
        atomic_json(ready_file,{'status':'ready','url':url,'pid':os.getpid(),
                    'home':str(home),'version':app.config.APP_VERSION,'build':app.config.APP_BUILD})
    app.run(open_browser=False,stop_event=stop,ready_callback=ready)
    return 0


def webview_runtime_available():
    if os.name!='nt':return False
    import winreg
    key=r'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for access in (winreg.KEY_READ|winreg.KEY_WOW64_32KEY,winreg.KEY_READ|winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive,key,0,access) as handle:
                    if winreg.QueryValueEx(handle,'pv')[0] not in ('','0.0.0.0'):return True
            except OSError:pass
    return False


def ensure_webview(*,allow_evergreen=False):
    fixed=resource_root()/'desktop/webview2'
    if str(fixed.resolve()).startswith('\\\\'):
        raise RuntimeError('WebView2 不支持从网络共享路径运行，请安装到本地磁盘。')
    manifest=fixed/'runtime-manifest.json'
    if manifest.is_file():
        document=json.loads(manifest.read_text('utf8'))
        if document.get('format')!='seedance.webview2-fixed@1' or not document.get('files'):
            raise RuntimeError('Fixed WebView2 runtime manifest is invalid')
        for name,checksum in document['files'].items():
            path=fixed/name
            if (not path.resolve().is_relative_to(fixed.resolve()) or path.is_symlink()
                or not path.is_file() or file_digest(path)!=checksum):
                raise RuntimeError('Fixed WebView2 runtime is incomplete or changed; reinstall this version to repair it')
        if not (fixed/'msedgewebview2.exe').is_file():raise RuntimeError('Fixed WebView2 executable is missing')
        if str(fixed.resolve()).startswith('\\\\'):
            raise RuntimeError('Microsoft Fixed WebView2 does not support installation on a network/UNC path')
        # Microsoft requires these exact AppContainer read/execute ACLs for Win10.
        # Never grant permissions to the user profile or application data root.
        if os.name=='nt' and sys.getwindowsversion().build<22000:
            for sid in ('*S-1-15-2-2:(OI)(CI)(RX)','*S-1-15-2-1:(OI)(CI)(RX)'):
                subprocess.run(['icacls.exe',str(fixed),'/grant',sid],check=True,timeout=30,
                    stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
        os.environ['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER']=str(fixed)
        return fixed
    if not allow_evergreen:
        raise RuntimeError('完整安装包缺少锁定的 WebView2 组件，请重新安装此版本。用户数据已保留。')
    if webview_runtime_available():return None
    root=resource_root(); path=root/'desktop/prerequisites/MicrosoftEdgeWebview2Setup.exe'
    manifest=json.loads((root/'desktop/prerequisites/manifest.json').read_text('utf8'))
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:
        raise RuntimeError('WebView2 prerequisite checksum mismatch')
    result=subprocess.run([str(path),'/silent','/install'],timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode!=0 or not webview_runtime_available():
        raise RuntimeError('WebView2 自动安装未完成。请连接网络后重试；用户数据已保留。')


def validate_backend_health(report,home,expected_pid):
    parsed=urlsplit(report.get('url',''))
    if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password
        or parsed.path not in ('','/') or parsed.query or parsed.fragment or not parsed.port
        or report.get('pid')!=expected_pid or Path(report.get('home','')).resolve()!=Path(home).resolve()):
        raise RuntimeError('Desktop backend readiness identity mismatch')
    # Local IPC must never inherit HTTP_PROXY, HTTPS_PROXY or Windows registry proxies.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(report['url']+'/api/bootstrap-health',timeout=5) as response:
        if response.status!=200:raise RuntimeError('Local backend health status failed')
        document=json.loads(response.read(65536).decode('utf8'))
    actual=document.get('data') or {}
    if (not document.get('ok') or actual.get('pid')!=expected_pid or actual.get('port')!=parsed.port
        or actual.get('version')!=report.get('version') or actual.get('build')!=report.get('build')
        or actual.get('workspace')!='owner'
        or Path(actual.get('data_dir','')).resolve()!=(Path(home)/'data').resolve()
        or Path(actual.get('root_dir','')).resolve()!=resource_root().resolve()):
        raise RuntimeError('Desktop health response belongs to a different service or data directory')
    return actual


def backend_environment(shutdown):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    values=[wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(),*[ctypes.byref(value) for value in values]):
        raise ctypes.WinError(ctypes.get_last_error())
    creation=(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
    return dict(os.environ,STUDIO_DESKTOP_STOP_FILE=str(Path(shutdown).resolve()),
        STUDIO_DESKTOP_PARENT_PID=str(os.getpid()),STUDIO_DESKTOP_PARENT_BIRTHSTAMP=str(creation))


def headless_supervisor(home,options):
    """Headless acceptance uses the same child-tree ownership as the GUI host."""
    session=Path(home)/'desktop-sessions'/secrets.token_hex(12);session.mkdir(parents=True)
    command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]
    command+=['--backend','--data-home',str(home),'--ready-file',str(options.ready_file),'--shutdown-file',str(options.shutdown_file)]
    process=None;job=None
    with (session/'backend.log').open('w',encoding='utf8') as log:
        try:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True,
                                     env=backend_environment(options.shutdown_file))
            job=ChildJob(process)
            started=time.monotonic()
            while process.poll() is None:
                if not options.ready_file.exists() and time.monotonic()-started>180:
                    raise TimeoutError('Headless backend startup exceeded 180 seconds')
                time.sleep(.2)
            return process.returncode
        finally:
            if process and process.poll() is None:
                options.shutdown_file.touch()
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
            if job:job.close()


def desktop(home, options):
    if os.name=='nt':
        identity=ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        identity.argtypes=[wintypes.LPCWSTR];identity.restype=ctypes.c_long
        identity('SeedanceStudio.Desktop')
    notice=LoadingNotice();notice.start()
    session=Path(home)/'desktop-sessions'/secrets.token_hex(12);session.mkdir(parents=True)
    trace=StartupTrace(session/'desktop-timing.json')
    trace.add('python_entry_to_gui_host',time.perf_counter()-ENTRY_STARTED)
    preparation=WebViewPreparation(trace,options.allow_evergreen)
    ready=session/'ready.json';shutdown=session/'shutdown'
    command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]
    command+=['--backend','--data-home',str(home),'--ready-file',str(ready),'--shutdown-file',str(shutdown)]
    log=(session/'backend.log').open('w',encoding='utf8')
    process=None;job=None;browser_owner=None
    try:
        notice.message='正在校验本地组件并启动服务；无需下载或安装 Python 包。'
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True,
                                 env=backend_environment(shutdown))
        job=ChildJob(process)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('后台启动失败，请查看 '+str(session/'backend.log'))
            if ready.exists():break
            time.sleep(.2)
        else:raise TimeoutError('后台启动超过180秒，用户数据已保留。日志：'+str(session/'backend.log'))
        report=json.loads(ready.read_text('utf8'))
        with trace.measure('application_health_handshake'):
            validate_backend_health(report,home,process.pid)
        trace.add('backend_identity_ready',time.monotonic()-(deadline-180))
        notice.message='本地服务已就绪，正在完成界面组件验证。'
        fixed=preparation.result(deadline-time.monotonic())
        if fixed:
            from desktop.process_ownership import BrowserProcessOwner
            browser_owner=BrowserProcessOwner(fixed/'msedgewebview2.exe',ChildJob(),session/'browser-processes.json')
        notice.close()
        if options.ready_file:atomic_json(options.ready_file,dict(report,status='starting',desktop_pid=os.getpid(),gui='edgechromium-starting'))
        import webview
        webview.settings['ALLOW_FILE_URLS']=False
        webview.settings['WEBVIEW2_RUNTIME_PATH']=str(fixed) if fixed else None
        webview.settings['REMOTE_DEBUGGING_PORT']=options.gui_cdp_port
        window=webview.create_window('Seedance Studio',report['url'],width=1380,height=900,min_size=(1000,680),
                                   text_select=True,background_color='#F5F6FA')
        startup_errors=[];closing=threading.Event();initial_document_ready=threading.Event();gui_failed=threading.Event();failure_lock=threading.RLock()
        def fail_gui(message,code):
            with failure_lock:
                if gui_failed.is_set():return
                startup_errors.append(message);gui_failed.set()
            trace.add(code,status='FAIL')
            if options.ready_file:
                try:atomic_json(options.ready_file,dict(report,status='failed',desktop_pid=os.getpid(),gui=code))
                except OSError:pass
        def on_loaded():
            if initial_document_ready.is_set() or gui_failed.is_set():return
            page={}
            runtime_version='unknown'
            try:
                page=window.evaluate_js("({title:document.title,ready:document.readyState,origin:location.origin,application:document.body?.dataset.application,width:innerWidth,height:innerHeight,buttons:[...document.querySelectorAll('aside button,nav button,.nav-item')].map(x=>x.textContent.trim()).filter(Boolean)})")
                validate_gui_page(page,report['url'])
                with failure_lock:
                    if gui_failed.is_set():return
                trace.add('gui_application_document_ready')
            except Exception as error:
                fail_gui(str(error),'gui_document_invalid')
                if options.gui_evidence:atomic_json(options.gui_evidence/'gui-page.json',{'status':'FAIL','error':str(error)})
                window.destroy();return
            try:
                from System import Func,Object
                native=window.native.webview
                runtime_version=str(native.Invoke(Func[Object](lambda:native.CoreWebView2.Environment.BrowserVersionString)))
            except Exception:pass
            if options.gui_evidence:
                try:
                    destination=options.gui_evidence.resolve();destination.mkdir(parents=True,exist_ok=True)
                    from System import Func,Object
                    from System.IO import FileStream,FileMode,FileAccess
                    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                    stream=FileStream(str(destination/'desktop-webview.png'),FileMode.Create,FileAccess.Write)
                    try:
                        native=window.native.webview
                        task=native.Invoke(Func[Object](lambda:native.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream)))
                        if not task.Wait(10000):raise TimeoutError('WebView2 preview capture timeout')
                    finally:stream.Dispose()
                    atomic_json(destination/'gui-page.json',{'status':'PASS','renderer':'EdgeChromium',
                        'browser_version':runtime_version,'fixed_runtime':str(fixed) if fixed else None,'page':page})
                except Exception as error:
                    atomic_json(options.gui_evidence/'gui-page.json',{'status':'FAIL','error':str(error)})
            with failure_lock:
                if not gui_failed.is_set():
                    if options.ready_file:
                        atomic_json(options.ready_file,dict(report,desktop_pid=os.getpid(),gui='edgechromium-loaded',
                            browser_version=runtime_version,fixed_runtime=str(fixed) if fixed else None,page=page))
                    initial_document_ready.set()
                    trace.add('gui_ready_committed')
        window.events.loaded+=on_loaded
        def close_when_requested():
            end=time.monotonic()+options.gui_smoke_seconds if options.gui_smoke_seconds else float('inf')
            while not closing.is_set():
                code=process.poll()
                if code is not None:
                    intentional=shutdown.exists() or (options.shutdown_file and Path(options.shutdown_file).exists())
                    if not intentional:
                        fail_gui('本地后台意外退出（代码 '+str(code)+'），用户数据已保留。日志：'+str(session/'backend.log'),'backend_exited')
                        if not options.gui_evidence:
                            ctypes.windll.user32.MessageBoxW(None,startup_errors[-1],'Seedance Studio',0x10)
                    try:window.destroy()
                    except Exception:pass
                    return
                if time.monotonic()>=end or (options.shutdown_file and Path(options.shutdown_file).exists()):
                    window.destroy();return
                time.sleep(.3)
        # Also supervise normal launches: an exited backend must not leave a
        # convincing but unusable desktop window on screen indefinitely.
        threading.Thread(target=close_when_requested,daemon=True).start()
        def request_gui_close():
            with failure_lock:
                if initial_document_ready.is_set() or closing.is_set():return False
                fail_gui('界面加载超时，用户数据已保留。请重新启动并保留启动日志：'+str(session),'gui_load_timeout')
            shutdown.touch()
            # Closing a stalled native UI must not block the watchdog itself.
            def close_window():
                try:window.destroy()
                except Exception:pass
            threading.Thread(target=close_window,daemon=True).start()
            return True
        def force_gui_exit():
            trace.add('gui_load_forced_exit',status='FAIL')
            if browser_owner:
                try:browser_owner.close(grace=0,final_wait=1)
                except OSError:pass
            try:(Path(home)/'desktop-error.log').write_text(startup_errors[-1],encoding='utf8')
            except OSError:pass
            # Only this GUI process exits. Verified browser handles were reaped
            # above, including platforms that deny nested WebView2 jobs.
            os._exit(1)
        threading.Thread(target=guard_gui_readiness,
            args=(initial_document_ready,closing,options.gui_load_timeout,request_gui_close,force_gui_exit),
            name='desktop-gui-readiness-deadline',daemon=True).start()
        webview.start(gui='edgechromium',private_mode=False,storage_path=str(Path(home)/'webview2'),debug=False,
            icon=str(resource_root()/'assets/branding/seedance-studio.ico'))
        closing.set()
        if startup_errors:raise RuntimeError(startup_errors[0])
        return 0
    finally:
        active_error=sys.exc_info()[0] is not None
        cleanup_errors=[]
        notice.close()
        try:shutdown.touch()
        except OSError as error:cleanup_errors.append(str(error))
        if process:
            try:
                try:process.wait(timeout=12)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired) as error:cleanup_errors.append(str(error))
        if job:
            try:job.close()
            except OSError as error:cleanup_errors.append(str(error))
        if browser_owner:
            try:
                cleanup=browser_owner.close()
                if cleanup['remaining_after_cleanup']:cleanup_errors.append('本实例浏览器进程未能完成清理')
            except OSError as error:cleanup_errors.append(str(error))
        log.close()
        if cleanup_errors:
            trace.add('desktop_cleanup',status='FAIL')
            if not active_error:raise RuntimeError('本实例进程清理未完成，请保留日志：'+'; '.join(cleanup_errors))


def main(argv=None):
    restore_standard_streams()
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-home',type=Path)
    parser.add_argument('--headless',action='store_true')
    parser.add_argument('--backend',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--ready-file',type=Path)
    parser.add_argument('--shutdown-file',type=Path)
    parser.add_argument('--gui-smoke-seconds',type=int,default=0)
    parser.add_argument('--gui-load-timeout',type=int,default=120)
    parser.add_argument('--gui-evidence',type=Path)
    parser.add_argument('--gui-cdp-port',type=int)
    parser.add_argument('--allow-evergreen',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--worker-module',choices=sorted(WORKER_MODULES))
    parser.add_argument('--update-helper',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--plan',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--ensure-webview',action='store_true')
    parser.add_argument('--migrate-from',type=Path)
    parser.add_argument('--skip-migration-prompt',action='store_true')
    parser.add_argument('--component-health',choices=('asr','browser'),help=argparse.SUPPRESS)
    parser.add_argument('--component-directory',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--health-report',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--component-proof',help=argparse.SUPPRESS)
    parser.add_argument('--component-baseline',action='store_true',help=argparse.SUPPRESS)
    options,extra=parser.parse_known_args(argv)
    if options.component_proof and (not options.component_health or not re.fullmatch('[a-f0-9]{64}',options.component_proof)):
        parser.error('Component proof requires a trusted stored proof identity and a health worker')
    if options.component_baseline and (not options.component_health or options.component_proof):
        parser.error('Baseline health requires an exclusive embedded catalog selection')
    if not 5<=options.gui_load_timeout<=600:parser.error('界面加载超时必须为 5 到 600 秒')
    if options.component_health and (not options.component_directory or not options.health_report or not options.data_home
        or options.backend or options.headless or options.update_helper or options.ensure_webview or options.worker_module):
        parser.error('Component health worker requires only explicit component, report and data-home paths')
    if bool(options.update_helper)!=bool(options.plan) or (options.update_helper and
        (options.backend or options.headless or options.ensure_webview or options.gui_cdp_port)):
        parser.error('Update helper requires only a verified --plan and its isolated user data home')
    if options.gui_cdp_port is not None and (not 1024<=options.gui_cdp_port<=65535
        or not options.gui_evidence or not options.data_home or options.headless or options.backend or options.ensure_webview):
        parser.error('GUI CDP requires an explicit isolated --data-home and --gui-evidence; port must be 1024..65535')
    if options.worker_module:
        sys.argv=[options.worker_module,*extra];runpy.run_module(options.worker_module,run_name='__main__');return 0
    if extra:parser.error('Unrecognized arguments')
    home=prepare_environment(options.data_home)
    if options.component_health:
        if os.name=='nt':ctypes.windll.kernel32.SetErrorMode(1|2|0x8000)
        from core.component_health import worker
        return worker(resource_root(),home,options.component_health,options.component_directory,options.health_report,options.component_proof,options.component_baseline)
    if options.update_helper:
        from desktop.update_helper import main as update_main
        return update_main(['--plan',str(options.plan)])
    if options.ensure_webview:ensure_webview(allow_evergreen=options.allow_evergreen);return 0
    if options.backend:
        if not options.ready_file or not options.shutdown_file:parser.error('Backend readiness/control paths are required')
        return backend(home,options.ready_file,options.shutdown_file)
    for control in (options.ready_file,options.shutdown_file):
        if control and control.exists():
            raise FileExistsError('Desktop control path already exists; use a new session path: '+str(control))
    with ExitStack() as stack:
        stack.enter_context(InstanceLock(home))
        default_home=Path(os.getenv('LOCALAPPDATA',str(Path.home())))/'SeedanceStudio'
        if home==default_home.resolve():
            default_lock=InstanceLock(home);default_lock.name='Local\\SeedanceStudioDesktopDefault'
            stack.enter_context(default_lock)
        maybe_migrate(home,options.migrate_from,prompt=not options.headless and not options.skip_migration_prompt)
        if options.headless:
            if not options.ready_file or not options.shutdown_file:parser.error('Headless mode requires readiness/control paths')
            return headless_supervisor(home,options)
        return desktop(home,options)


if __name__=='__main__':
    # A frozen windowed application has no stdout/stderr; capture startup errors.
    home=None
    try:
        raise SystemExit(main())
    except Exception as exc:
        import traceback
        text=traceback.format_exc()
        home=Path(os.environ.get('STUDIO_DATA_HOME',str(Path.home()/'SeedanceStudio')))
        try:
            home.mkdir(parents=True,exist_ok=True)
            filename=('desktop-backend-error.log' if '--backend' in sys.argv else
                'desktop-update-error.log' if '--update-helper' in sys.argv else
                'desktop-component-health-error.log' if '--component-health' in sys.argv else 'desktop-error.log')
            (home/filename).write_text(text,encoding='utf8')
            print(text,file=sys.stderr,flush=True)
        except OSError:pass
        if not any(arg in sys.argv for arg in ('--headless','--backend','--worker-module','--update-helper','--ensure-webview','--gui-smoke-seconds','--gui-evidence','--component-health')) and os.name=='nt':
            ctypes.windll.user32.MessageBoxW(None,str(exc)+'\n\n'+str(home/'desktop-error.log'),'Seedance Studio',0x10)
        raise SystemExit(1)
