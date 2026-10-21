"""Cross-process reader/writer leases, released by the OS on abnormal termination."""
from contextlib import contextmanager
import os
from pathlib import Path


class ComponentBusy(RuntimeError):
    pass


@contextmanager
def file_lease(path,*,shared=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            import msvcrt
            class Overlapped(ctypes.Structure):
                _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
                          ('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),('hEvent',wintypes.HANDLE)]
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.LockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype=wintypes.BOOL
            kernel.UnlockFileEx.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype=wintypes.BOOL
            handle=msvcrt.get_osfhandle(stream.fileno());overlapped=Overlapped()
            if not kernel.LockFileEx(handle,1|(0 if shared else 2),0,1,0,ctypes.byref(overlapped)):
                error=ctypes.get_last_error()
                if error in {32,33,158}:raise ComponentBusy('组件正在使用或维护，暂不能并行修改')
                raise ctypes.WinError(error)
            try:yield
            finally:
                if not kernel.UnlockFileEx(handle,0,1,0,ctypes.byref(overlapped)):raise ctypes.WinError(ctypes.get_last_error())
        else:
            import fcntl
            try:fcntl.flock(stream,fcntl.LOCK_NB|(fcntl.LOCK_SH if shared else fcntl.LOCK_EX))
            except OSError as exc:raise ComponentBusy('组件正在使用或维护，暂不能并行修改') from exc
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)
