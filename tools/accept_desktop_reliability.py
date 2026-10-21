"""Real frozen GUI/backend crash recovery plus real-TCP health identity red team.

No provider requests, user profiles or production process IDs are used.
"""
from __future__ import annotations
import argparse,ctypes,json,os,sqlite3,subprocess,sys,threading,time
from ctypes import wintypes as w
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop import launcher
from accept_desktop_exe import Application,clean_environment


def process_handles(parent,backend,executable):
    """Hold kernel handles so PID reuse cannot target another process."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',ctypes.c_size_t),
            ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',w.LONG),('flags',w.DWORD),('exe',w.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
    kernel.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
    kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==w.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    rows={}
    try:
        row=Entry();row.size=ctypes.sizeof(row);more=kernel.Process32FirstW(snapshot,ctypes.byref(row))
        while more:
            rows[row.pid]=row.parent;more=kernel.Process32NextW(snapshot,ctypes.byref(row))
    finally:kernel.CloseHandle(snapshot)
    assert rows.get(backend)==parent,'Backend must be this newly launched GUI child'
    owned={parent}
    while True:
        found={pid for pid,ppid in rows.items() if ppid in owned}
        if found<=owned:break
        owned|=found
    handles={}
    try:
        for pid in owned:
            handle=kernel.OpenProcess(0x101001 if pid==backend else 0x101000,False,pid)
            if handle:handles[pid]=handle
        births={}
        for pid,handle in handles.items():
            values=[w.FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(handle,*[ctypes.byref(x) for x in values]):
                births[pid]=(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
        valid={parent}
        while True:
            found={pid for pid in handles if rows.get(pid) in valid and pid in births
                and rows[pid] in births and births[pid]>=births[rows[pid]]}
            if found<=valid:break
            valid|=found
        for pid in set(handles)-valid:kernel.CloseHandle(handles.pop(pid))
        assert backend in handles
        buffer=ctypes.create_unicode_buffer(32768);size=w.DWORD(len(buffer))
        assert kernel.QueryFullProcessImageNameW(handles[backend],0,buffer,ctypes.byref(size))
        assert Path(buffer.value).resolve()==executable.resolve(),'Another program must never be terminated'
        identities={}
        for pid,handle in handles.items():
            image=ctypes.create_unicode_buffer(32768);length=w.DWORD(len(image))
            ok=kernel.QueryFullProcessImageNameW(handle,0,image,ctypes.byref(length))
            identities[pid]={'parent_pid':rows.get(pid),'birth_100ns':births.get(pid),'image':image.value if ok else 'unavailable'}
        process_handles.identities=identities
        return kernel,handles
    except BaseException:
        for handle in handles.values():kernel.CloseHandle(handle)
        raise


def false_health(evidence):
    """Exercise the shipped health validator against a genuine local HTTP peer."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            data={'ok':True,'data':{'pid':-1,'port':self.server.server_port,'workspace':'owner',
                'version':'5.2.0','build':'wrong','data_dir':str(evidence/'data'),'root_dir':str(launcher.resource_root())}}
            body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    report={'url':'http://127.0.0.1:'+str(server.server_port),'pid':os.getpid(),'home':str(evidence),'version':'5.2.0','build':'candidate'}
    try:
        try:launcher.validate_backend_health(report,evidence,os.getpid())
        except RuntimeError as error:
            assert 'different service' in str(error)
            return {'status':'PASS','scope':'Exact source health validator with real localhost TCP; not an injected frozen backend',
                'HTTP_200_ok_true_rejected':True,'reason':'PID/version identity mismatch'}
        raise AssertionError('HTTP 200 alone was incorrectly accepted')
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--exe',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);a=p.parse_args()
    if os.name!='nt':p.error('Actual Windows is required')
    proof=a.evidence.resolve();proof.mkdir(parents=True,exist_ok=False);home=proof/'Isolated User Data'
    report={'scope':'Real frozen GUI and backend on current Windows; not clean OS/reboot','paid_calls':0,'checks':[]}
    ready=proof/'gui-ready.json';stop=proof/'gui-stop';process=None;handles={};kernel=None
    try:
        report['checks'].append({'name':'false backend readiness','details':false_health(proof),'status':'PASS'})
        with (proof/'gui-process.log').open('w',encoding='utf8') as log:
            process=subprocess.Popen([str(a.exe),'--data-home',str(home),'--ready-file',str(ready),'--shutdown-file',str(stop),
                '--skip-migration-prompt','--gui-evidence',str(proof/'gui'),'--gui-smoke-seconds','180'],env=clean_environment(),stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('GUI exited before application readiness')
                if ready.exists():
                    info=json.loads(ready.read_text('utf8'))
                    if info.get('gui')=='edgechromium-loaded':break
                time.sleep(.2)
            else:raise TimeoutError('GUI readiness deadline')
            assert info['desktop_pid']==process.pid and Path(info['home']).resolve()==home
            helper=Application(a.exe,home,proof,'live-gui');helper.info=info;helper.token=helper.request('/api/hypit/session')['token']
            health=helper.request('/api/bootstrap-health');assert health['pid']==info['pid']
            doc=helper.request('/api/configuration')
            for provider in doc['providers']:provider['models']['chat']='recovery-preserved-model'
            helper.request('/api/configuration',{'document':doc,'expected_revision':doc['revision']})
            kernel,handles=process_handles(process.pid,info['pid'],a.exe)
            report['tracked_processes_before_injection']=process_handles.identities
            assert kernel.TerminateProcess(handles[info['pid']],0xE001)
            code=process.wait(timeout=25);assert code!=0,'Unexpected backend failure must not be reported as successful exit'
            remaining=[]
            for pid,handle in handles.items():
                if kernel.WaitForSingleObject(handle,12000)!=0:remaining.append(pid)
            assert not remaining,('Owned processes remained',remaining)
            report['checks'].append({'name':'real backend crash closes GUI and owned processes','status':'PASS',
                'details':{'injected_backend_only':info['pid'],'gui_exit_code':code,'held_process_handles':len(handles),'remaining_owned_processes':remaining}})
        restarted=Application(a.exe,home,proof,'after-crash')
        try:
            restarted.start();doc=restarted.request('/api/configuration')
            assert all(x['models']['chat']=='recovery-preserved-model' for x in doc['providers'])
        finally:restarted.close()
        with sqlite3.connect(home/'data/studio.db') as db:
            assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
            assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM usage_events').fetchone()[0]==0
        report['checks'].append({'name':'crash restart retains saved configuration and valid database','status':'PASS'})
        report['status']='PASS'
    except BaseException as error:
        report.update(status='FAIL',error=str(error));raise
    finally:
        stop.touch()
        if process and process.poll() is None:
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        for handle in handles.values():kernel.CloseHandle(handle)
        (proof/'reliability.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'status':report['status'],'report':str(proof/'reliability.json')}))


if __name__=='__main__':main()
