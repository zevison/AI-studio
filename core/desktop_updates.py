"""Application adapter for the trusted update service; no frontend path inputs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from . import config,db,team_safety
from .update_service import UpdateService
from .update_security import UpdateBlocked,UpdateRejected,plain_path,sha256

_lock=threading.Lock()
_instance=None
_identity=None


def service():
    global _instance,_identity
    key=(config.USER_HOME,config.ROOT,config.APP_VERSION,config.APP_BUILD)
    with _lock:
        if _identity!=key:
            _instance=UpdateService(config.USER_HOME,config.ROOT,current_version=config.APP_VERSION,
                current_build=config.APP_BUILD,current_schema=db.CURRENT_SCHEMA_VERSION,
                control_path=team_safety.control_path())
            _identity=key
        return _instance


def launch_prepared(result):
    """Launch only the exact verified runner and plan created by the service."""
    identifier=result.get('transaction_id')
    if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):
        raise UpdateRejected('更新事务标识无效')
    home=plain_path(config.USER_HOME)
    runner=plain_path(home/'updates/runners'/identifier/'SeedanceStudio.exe')
    plan=plain_path(home/'updates/plans'/(identifier+'.json'))
    if plain_path(result['helper_exe'])!=runner or plain_path(result['plan'])!=plan:
        raise UpdateRejected('更新 helper 或事务路径越界')
    document=json.loads(plan.read_text('utf8'))
    if document.get('id')!=identifier or document.get('state')!='prepared':
        raise UpdateRejected('更新计划尚未就绪')
    from desktop.update_helper import validate_runner
    validate_runner(document,runner)
    # BREAKAWAY is allowed by the owning desktop's job. Without it, closing
    # the old backend would kill the independent updater mid-installation.
    process=subprocess.Popen([str(runner),'--data-home',str(home),'--update-helper','--plan',str(plan)],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0)|getattr(subprocess,'CREATE_NO_WINDOW',0),
        close_fds=True)
    return {'pid':process.pid,'state':'started_not_yet_installed'}


def begin_install(target_id,confirmed=False):
    if not getattr(sys,'frozen',False) or os.environ.get('STUDIO_DESKTOP')!='1':
        raise UpdateBlocked('自动安装仅适用于完整 Windows 桌面 EXE，开发网页不会替换程序')
    program=plain_path(Path(sys.executable).parent)
    if program.parent.name!='versions' or program.name!=config.APP_VERSION+'-'+config.APP_BUILD:
        raise UpdateBlocked('当前程序未通过标准 EXE 安装入口安装，请使用完整安装包')
    return service().begin_install(target_id,confirmed=confirmed,on_prepared=launch_prepared,
        stop_file=os.environ.get('STUDIO_DESKTOP_STOP_FILE'),
        parent_pid=os.environ.get('STUDIO_DESKTOP_PARENT_PID'),
        parent_birthstamp=os.environ.get('STUDIO_DESKTOP_PARENT_BIRTHSTAMP'),
        install_root=program.parent.parent,program_root=program)
