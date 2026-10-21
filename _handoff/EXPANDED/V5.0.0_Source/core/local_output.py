"""Local output-folder opening, always asynchronous to the HTTP request.

This module never makes network calls or contacts a paid provider. It opens only
our configured OUTPUT_DIR, never paths supplied by the browser.
"""
from __future__ import annotations
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from . import config

_log=logging.getLogger('seedance-output')

def _append_log(message:str):
    try:
        with (config.DATA_DIR/'output_open.log').open('a',encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+message+'\n')
    except OSError:
        pass

def _launch(folder:Path):
    """One OS GUI action, outside HTTP handler; no arbitrary user input."""
    path=str(folder)
    if os.name=='nt':
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except (OSError,AttributeError) as first:
            _append_log('os.startfile failed: '+repr(first))
            flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
            subprocess.Popen(['explorer.exe',path],creationflags=flags,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    elif sys.platform=='darwin':
        subprocess.Popen(['open',path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(['xdg-open',path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def request_open():
    """Return quickly; background errors go to a local diagnostic log."""
    folder=config.OUTPUT_DIR.resolve()
    folder.mkdir(parents=True,exist_ok=True)
    _append_log('accepted folder='+str(folder))
    def work():
        try:
            _launch(folder)
            _append_log('OS open command dispatched')
        except Exception as e:
            _append_log('OS open command FAILED: '+repr(e))
            _log.exception('failed to open local output folder')
    threading.Thread(target=work,name='open-local-output',daemon=True).start()
    return {'accepted':True,'path':str(folder),'note':'已请求系统打开文件夹；如未弹出可复制此路径在资源管理器中打开。'}
