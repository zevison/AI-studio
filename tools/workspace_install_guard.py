"""Prevent shared native runtime updates while ANY workspace process is alive."""
from pathlib import Path
import ctypes,os

def _matching_live_pid(file):
    try:pid=int(file.read_text(encoding='ascii').strip())
    except (ValueError,OSError):return True  # An unreadable marker must be inspected, not ignored.
    if pid<=0:return True
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
        except PermissionError:return True
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return ctypes.get_last_error()!=87
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return True
        if code.value!=259:return False
        stamps=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(x) for x in stamps]):return True
        born=((stamps[0].dwHighDateTime<<32)+stamps[0].dwLowDateTime)/1e7-11644473600
        if abs(file.stat().st_mtime-born)>180:return False
        length=wintypes.DWORD(32768);image=ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle,0,image,ctypes.byref(length)):return True
        return Path(image.value).name.lower() in ('python.exe','pythonw.exe')
    finally:kernel.CloseHandle(handle)

def any_workspace_running(root):
    root=Path(root)
    candidates=[root/'seedance_studio.pid',*(root/'data/workspaces').glob('*/seedance_studio.pid')]
    return any(_matching_live_pid(p) for p in candidates if p.is_file())
