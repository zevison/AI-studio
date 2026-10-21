from __future__ import annotations
import json, os, socket, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def env_file():
    out={}
    p=ROOT/'.env'
    if p.exists():
        for raw in p.read_text(encoding='utf-8-sig',errors='ignore').splitlines():
            line=raw.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k,v=line.split('=',1); out[k.strip()]=v.strip().strip('"').strip("'")
    return out

def free(port):
    s=socket.socket(); s.settimeout(.15)
    try:
        return s.connect_ex(('127.0.0.1',port))!=0
    finally:s.close()

def status(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status',timeout=.6) as r:
            j=json.loads(r.read().decode('utf-8'))
            return j.get('data') or {}
    except Exception:return None

def existing_instance():
    rp=ROOT/'.runtime_port'
    if not rp.exists():return None,None
    try:port=int(rp.read_text(encoding='ascii').strip())
    except Exception:return None,None
    st=status(port)
    if st and Path(str(st.get('root_dir') or '')).resolve()==ROOT.resolve():
        return port,st
    return None,None


def choose_port(preferred):
    if free(preferred): return preferred,None
    st=status(preferred)
    for p in list(range(8780,8820))+list(range(9000,9030)):
        if free(p): return p,st
    raise RuntimeError('没有找到可用的本地端口。请先停止旧版 Seedance Studio。')

def wait(port,seconds=12):
    end=time.time()+seconds
    while time.time()<end:
        st=status(port)
        if st:return st
        time.sleep(.25)
    return None

def main():
    envvals=env_file(); preferred=int(envvals.get('PORT','8765') or 8765)
    running_port,running=existing_instance()
    if running:
        url=f'http://127.0.0.1:{running_port}/?v={running.get("version","current")}'
        print(f'[OK] 本目录 Seedance Studio V{running.get("version")} 已在运行，直接打开现有服务：{url}')
        webbrowser.open_new(url);return
    port,old=choose_port(preferred)
    if old:
        print(f'[INFO] 检测到 {preferred} 端口已有 Seedance Studio V{old.get("version","?")} 在运行。')
        print(f'[INFO] 为避免打开旧版本，本次自动使用新端口 {port}。')
    env=os.environ.copy(); env['PORT']=str(port); env['OPEN_BROWSER']='0'
    flags=0
    if os.name=='nt': flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'DETACHED_PROCESS',0)
    (ROOT/'.watchdog_stop').unlink(missing_ok=True)
    subprocess.Popen([sys.executable,str(ROOT/'tools'/'watchdog.py')],cwd=str(ROOT),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    st=wait(port,seconds=20)
    if not st: raise RuntimeError('新版本服务没有在 12 秒内启动成功，请查看 data/startup.log 查看错误。')
    url=f'http://127.0.0.1:{port}/?v={st.get("version","current")}'
    (ROOT/'.runtime_port').write_text(str(port),encoding='ascii')
    (ROOT/'data'/'current_url.txt').write_text(url,encoding='utf-8')
    print(f'[OK] Seedance Studio V{st.get("version")} 已启动：{url}')
    print(f'[OK] 实际目录：{st.get("root_dir",ROOT)}')
    webbrowser.open_new(url)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('[ERROR]',e); input('按回车键退出…'); raise SystemExit(1)
