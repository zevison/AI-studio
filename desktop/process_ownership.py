"""Own only this GUI's verified WebView2 process family, including before load."""
from __future__ import annotations
import ctypes,json,os,threading,time
from ctypes import wintypes as w
from pathlib import Path


def browser_identity_matches(image,expected,birth,parent_birth):
    return (parent_birth is not None and birth is not None and birth>=parent_birth
            and Path(image).resolve()==Path(expected).resolve())


class WindowsProcesses:
    def __init__(self):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        class Entry(ctypes.Structure):
            _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',ctypes.c_size_t),
                ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',w.LONG),('flags',w.DWORD),('exe',w.WCHAR*260)]
        self.Entry=Entry
        k=self.k;k.CreateToolhelp32Snapshot.restype=w.HANDLE
        k.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)];k.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
        k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
        k.CloseHandle.argtypes=[w.HANDLE];k.GetCurrentProcess.restype=w.HANDLE
        k.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
        k.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
        k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];k.TerminateProcess.argtypes=[w.HANDLE,w.UINT]

    def snapshot(self):
        handle=self.k.CreateToolhelp32Snapshot(2,0)
        if handle==w.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        rows=[]
        try:
            row=self.Entry();row.size=ctypes.sizeof(row);more=self.k.Process32FirstW(handle,ctypes.byref(row))
            while more:
                rows.append({'pid':row.pid,'parent':row.parent,'name':row.exe});more=self.k.Process32NextW(handle,ctypes.byref(row))
        finally:self.close(handle)
        return rows

    def open(self,pid):
        # SET_QUOTA permits job assignment; the fallback still permits exact-
        # handle termination. Neither operation ever searches by process name.
        return self.k.OpenProcess(0x101101,False,pid) or self.k.OpenProcess(0x101001,False,pid)

    def birth(self,handle):
        values=[w.FILETIME() for _ in range(4)]
        if not self.k.GetProcessTimes(handle,*[ctypes.byref(x) for x in values]):raise ctypes.WinError(ctypes.get_last_error())
        return (values[0].dwHighDateTime<<32)|values[0].dwLowDateTime

    def current_birth(self):return self.birth(self.k.GetCurrentProcess())

    def image(self,handle):
        buffer=ctypes.create_unicode_buffer(32768);size=w.DWORD(len(buffer))
        if not self.k.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value

    def exited(self,handle):return self.k.WaitForSingleObject(handle,0)==0
    def close(self,handle):self.k.CloseHandle(handle)
    def terminate(self,handle):
        if not self.exited(handle) and not self.k.TerminateProcess(handle,1):raise ctypes.WinError(ctypes.get_last_error())


class BrowserProcessOwner:
    def __init__(self,expected,job,report,*,api=None,owner_pid=None,owner_birth=None,start=True):
        self.expected=Path(expected).resolve();self.job=job;self.report=Path(report)
        self.api=api or WindowsProcesses();self.owner_pid=owner_pid or os.getpid()
        self.owner_birth=self.api.current_birth() if owner_birth is None else owner_birth
        self.members={};self.events=[];self.lock=threading.RLock();self.stop=threading.Event();self.closed=False
        self.thread=None
        if start:
            self.thread=threading.Thread(target=self._watch,name='desktop-owned-webview-processes',daemon=True);self.thread.start()

    def _watch(self):
        while not self.stop.is_set():
            try:self.capture()
            except OSError as error:
                with self.lock:self.events=(self.events+[{'stage':'snapshot','error':str(error)}])[-128:]
            self.stop.wait(.25)

    def capture(self):
        with self.lock:
            if self.closed:return
            # Identify exited parents before the snapshot. Holding their handles
            # prevents PID reuse while their last children are being discovered.
            dead={pid for pid,row in self.members.items() if self.api.exited(row['handle'])}
            rows=self.api.snapshot();parents={row['parent'] for row in rows}
            births={self.owner_pid:self.owner_birth,**{pid:row['birth'] for pid,row in self.members.items()}}
            pending=[row for row in rows if row['name'].casefold()==self.expected.name.casefold() and row['pid'] not in self.members]
            while pending:
                progress=False
                for row in pending[:]:
                    if row['parent'] not in births:continue
                    pending.remove(row);handle=self.api.open(row['pid'])
                    if not handle:continue
                    keep=False
                    try:
                        birth=self.api.birth(handle);image=self.api.image(handle)
                        if not browser_identity_matches(image,self.expected,birth,births[row['parent']]):continue
                        assigned=True
                        try:self.job.add_process(handle)
                        except OSError as error:
                            assigned=False;self.events.append({'stage':'job-fallback','pid':row['pid'],'error':str(error)})
                        self.members[row['pid']]={'handle':handle,'birth':birth,'parent':row['parent'],'assigned':assigned}
                        births[row['pid']]=birth;keep=True;progress=True
                        self.events.append({'stage':'owned','pid':row['pid'],'birth':birth,'parent':row['parent'],'job_assigned':assigned})
                    except OSError:pass
                    finally:
                        if not keep:self.api.close(handle)
                if not progress:break
            for pid in dead-parents:
                self.api.close(self.members.pop(pid)['handle'])
            self.events=self.events[-128:]

    def close(self,grace=2,final_wait=3):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        try:self.capture()
        except OSError as error:self.events.append({'stage':'final-snapshot','error':str(error)})
        with self.lock:
            self.closed=True
            deadline=time.monotonic()+grace
            while time.monotonic()<deadline and any(not self.api.exited(row['handle']) for row in self.members.values()):time.sleep(.05)
            before=[pid for pid,row in self.members.items() if not self.api.exited(row['handle'])]
            errors=[]
            try:self.job.close()  # Includes the browser and all normally inherited children.
            except OSError as error:errors.append({'stage':'job-close','error':str(error)})
            for pid,row in self.members.items():
                if not self.api.exited(row['handle']):
                    try:self.api.terminate(row['handle'])
                    except OSError as error:errors.append({'pid':pid,'error':str(error)})
            deadline=time.monotonic()+final_wait
            while time.monotonic()<deadline and any(not self.api.exited(row['handle']) for row in self.members.values()):time.sleep(.05)
            remaining=[pid for pid,row in self.members.items() if not self.api.exited(row['handle'])]
            result={'format':'seedance.browser-process-ownership@1','owner_pid':self.owner_pid,'owner_birth':self.owner_birth,
                'scope':'Only verified image + held parent identity + creation time; no global browser termination',
                'events':self.events,'alive_after_disposal_grace':before,'remaining_after_cleanup':remaining,'errors':errors}
            for row in self.members.values():self.api.close(row['handle'])
            self.members.clear()
            try:
                self.report.parent.mkdir(parents=True,exist_ok=True);self.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            except OSError:pass
            return result
