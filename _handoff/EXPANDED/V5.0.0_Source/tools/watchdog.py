from __future__ import annotations
import json, os, signal, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STOP=ROOT/'.watchdog_stop'
PID=ROOT/'.watchdog_pid'
LOG=ROOT/'data'/'watchdog.log'


def log(msg):
    try:
        LOG.parent.mkdir(parents=True,exist_ok=True)
        with LOG.open('a',encoding='utf-8') as f:f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+msg+'\n')
    except Exception:pass


def hidden_flags():
    if os.name!='nt':return 0
    return getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'DETACHED_PROCESS',0)


def main():
    STOP.unlink(missing_ok=True);PID.write_text(str(os.getpid()),encoding='ascii')
    crashes=[]
    try:
        while not STOP.exists():
            env=os.environ.copy();env['OPEN_BROWSER']='0'
            log('starting server process')
            with (ROOT/'data'/'server_watchdog.log').open('a',encoding='utf-8') as out:
                p=subprocess.Popen([sys.executable,str(ROOT/'app.py')],cwd=str(ROOT),env=env,stdout=out,stderr=out,creationflags=(getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0))
                rc=p.wait()
            if STOP.exists():
                log(f'server stopped intentionally rc={rc}');break
            now=time.time();crashes=[x for x in crashes if now-x<90];crashes.append(now)
            log(f'server exited unexpectedly rc={rc}; crashes_90s={len(crashes)}')
            if len(crashes)>=5:
                log('crash loop protection triggered; watchdog exits')
                break
            time.sleep(2)
    finally:
        PID.unlink(missing_ok=True);STOP.unlink(missing_ok=True)

if __name__=='__main__':main()
