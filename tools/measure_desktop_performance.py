"""Measure the actual frozen GUI with isolated data; no model requests are made."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import time


def sample_tree(parent):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',ctypes.c_size_t),
                  ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),
                  ('priority',w.LONG),('flags',w.DWORD),('exe',w.WCHAR*260)]
    class Memory(ctypes.Structure):
        _fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(name,ctypes.c_size_t) for name in
            ('peak_working','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
    kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
    kernel.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    psapi.GetProcessMemoryInfo.argtypes=[w.HANDLE,ctypes.POINTER(Memory),w.DWORD]
    handle=kernel.CreateToolhelp32Snapshot(2,0);rows=[]
    if handle==w.HANDLE(-1).value:raise OSError(ctypes.get_last_error(),'process snapshot')
    try:
        entry=Entry();entry.size=ctypes.sizeof(entry)
        available=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while available:
            rows.append((entry.pid,entry.parent,entry.threads))
            available=kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:kernel.CloseHandle(handle)
    owned={parent}
    while True:
        found={pid for pid,ppid,_ in rows if ppid in owned}
        if found<=owned:break
        owned|=found
    memory=0;cpu=0;threads=0;unreadable=0
    for pid,_,count in rows:
        if pid not in owned:continue
        process=kernel.OpenProcess(0x410,False,pid)
        if not process:unreadable+=1;continue
        try:
            usage=Memory();usage.cb=ctypes.sizeof(usage)
            if psapi.GetProcessMemoryInfo(process,ctypes.byref(usage),usage.cb):memory+=usage.private
            else:unreadable+=1
            created,exited,kernel_time,user_time=(w.FILETIME() for _ in range(4))
            if kernel.GetProcessTimes(process,*map(ctypes.byref,(created,exited,kernel_time,user_time))):
                cpu+=sum((item.dwHighDateTime<<32)|item.dwLowDateTime for item in (kernel_time,user_time))/10_000_000
            threads+=count
        finally:kernel.CloseHandle(process)
    return {'processes':len(owned),'private_bytes_sum':memory,'cpu_seconds_alive_processes':cpu,
            'threads':threads,'unreadable_processes':unreadable}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--idle-seconds',type=int,default=20)
    parser.add_argument('--runs',type=int,default=5)
    options=parser.parse_args()
    if os.name!='nt':parser.error('Actual Windows is required')
    if not 5<=options.idle_seconds<=600:parser.error('Idle observation must be 5..600 seconds')
    if not 2<=options.runs<=10:parser.error('Runs must be 2..10')
    proof=options.evidence.resolve();proof.mkdir(parents=True,exist_ok=False)
    home=proof/'独立 性能 数据';results=[]
    env={key:value for key,value in os.environ.items() if not any(secret in key.upper() for secret in ('KEY','TOKEN','SECRET','PASSWORD'))
         and not key.upper().startswith(('STUDIO_','PYTHON','OPENLUX_','BRIGHTDATA_','OSS_'))}
    env.update(STUDIO_DATA_HOME=str(home),STUDIO_PROFILE_HOME=str(home))
    for index in range(options.runs):
        run=proof/('first-new-profile' if index==0 else 'repeat-'+str(index));run.mkdir()
        ready=run/'ready.json';stop=run/'stop';samples=[];started=time.monotonic();loaded=None
        next_sample=started
        with (run/'process.log').open('w',encoding='utf8') as log:
            process=subprocess.Popen([str(options.exe.resolve()),'--data-home',str(home),'--ready-file',str(ready),
                '--shutdown-file',str(stop),'--gui-evidence',str(run/'gui'),'--skip-migration-prompt',
                '--gui-smoke-seconds',str(options.idle_seconds+240)],env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                while time.monotonic()-started<options.idle_seconds+210:
                    if process.poll() is not None:raise RuntimeError('GUI exited before performance observation finished')
                    if time.monotonic()>=next_sample:
                        sample=sample_tree(process.pid);sample['seconds']=round(time.monotonic()-started,3);samples.append(sample)
                        next_sample=time.monotonic()+1
                    if ready.exists() and json.loads(ready.read_text('utf8')).get('gui')=='edgechromium-loaded':
                        if loaded is None:loaded=time.monotonic()
                        if time.monotonic()-loaded>=options.idle_seconds:break
                    time.sleep(.1)
                else:raise TimeoutError('GUI failed bounded readiness deadline')
            finally:
                stop.touch()
                try:process.wait(timeout=30)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                (run/'samples.json').write_text(json.dumps(samples,indent=2),encoding='utf8')
            results.append({'kind':run.name,'gui_ready_seconds':round(loaded-started,3) if loaded else None,
                'private_bytes_peak_sum':max((s['private_bytes_sum'] for s in samples),default=0),
                'max_processes':max((s['processes'] for s in samples),default=0),'exit_code':process.returncode,
                'samples_file':str(run/'samples.json')})
        (proof/'performance.json').write_text(json.dumps({'scope':'Observed current Windows host; '+str(options.runs)+' GUI launches, no paid calls. '
            'Private bytes sum covers live descendants; CPU excludes already exited workers. Not a clean OS or statistical benchmark.',
            'results':results},indent=2),encoding='utf8')
    print(proof)


if __name__=='__main__':main()
