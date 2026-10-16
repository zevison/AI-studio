from __future__ import annotations
import os, subprocess, time, urllib.request, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
STOP=ROOT/'.watchdog_stop';STOP.write_text('stop',encoding='ascii')
p=ROOT/'.runtime_port';ports=[]
if p.exists():
    try:ports.append(int(p.read_text().strip()))
    except:pass
ports += [8765]+list(range(8780,8820))+list(range(9000,9030))
seen=set();stopped=False
for port in ports:
    if port in seen:continue
    seen.add(port)
    try:
        with OPENER.open(f'http://127.0.0.1:{port}/api/bootstrap-health',timeout=1.2) as r:
            response=json.load(r)
        info=response.get('data') or {}
        other_root=info.get('root_dir')
        if not other_root or Path(other_root).resolve()!=ROOT.resolve():
            # Never shut down another Studio version: it may have paid jobs in flight.
            continue
        req=urllib.request.Request(f'http://127.0.0.1:{port}/api/shutdown',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
        with OPENER.open(req,timeout=1.5) as r:
            if r.status==200:
                print(f'[OK] 已停止本目录服务：{port}');stopped=True;break
    except Exception:pass
# If the HTTP server already crashed, still stop the watchdog so it cannot relaunch it.
wp=ROOT/'.watchdog_pid'
if wp.exists():
    try:
        pid=int(wp.read_text().strip())
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:os.kill(pid,15)
    except Exception:pass
    wp.unlink(missing_ok=True)
p.unlink(missing_ok=True)
time.sleep(.2)
print('[OK] 后台守护已停止。' if stopped else '[INFO] 服务未响应，但已发送停止标记并关闭后台守护。')
