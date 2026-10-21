"""Independent, verified runner for a user-confirmed Seedance update.

Run a hash-checked onedir copy, never the executable in the installation being
replaced. Production execution requires bundled TUF trust and Authenticode.
"""
from __future__ import annotations
import argparse
import ast
import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

from core.update_security import PRODUCT_ID, UpdateBlocked, UpdateRejected, plain_path, sha256, verify_package
from core.update_service import UpdateService, atomic_json, _gate_schema
from core.sqlite_snapshot import _native


def _source_constant(file,name):
    for node in ast.parse(Path(file).read_text('utf8')).body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id==name for target in node.targets):
            return ast.literal_eval(node.value)
    raise UpdateRejected('独立 helper 缺少应用版本合同')


def _process_identity(pid):
    if os.name!='nt':raise UpdateBlocked('更新 helper 仅支持 Windows')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return None
        raise UpdateBlocked('无法核实旧桌面进程身份，未关闭任何其他程序')
    try:
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        values=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(value) for value in values]):raise ctypes.WinError(ctypes.get_last_error())
        birth=(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
        buffer=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(buffer))
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(length)):raise ctypes.WinError(ctypes.get_last_error())
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):raise ctypes.WinError(ctypes.get_last_error())
        return {'birthstamp':str(birth),'executable':str(Path(buffer.value).resolve()),'running':code.value==259}
    finally:kernel.CloseHandle(handle)


def _stop_owned_parent(plan):
    pid=plan['parent_pid']
    if type(pid) is not int or pid<=0:raise UpdateRejected('旧桌面进程身份无效')
    expected=(Path(plan.get('program_root',plan['install_root']))/'SeedanceStudio.exe').resolve()
    info=_process_identity(pid)
    if info and (info['birthstamp']!=plan['parent_birthstamp'] or Path(info['executable']).resolve()!=expected):
        raise UpdateRejected('旧桌面 PID 已重用或属于其他程序，未发送停止信号')
    stop=plain_path(plan['stop_file'])
    if not stop.is_relative_to(Path(plan['home'])/'desktop-sessions'):raise UpdateRejected('停止信号越界')
    if info and info['running']:
        stop.touch()
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            current=_process_identity(pid)
            if current is None or not current['running']:return
            if current['birthstamp']!=info['birthstamp']:return
            time.sleep(.2)
        raise UpdateBlocked('旧桌面未在一分钟内优雅退出；未强杀进程，未开始安装')


@contextmanager
def _deny_package_replacement(path):
    if os.name!='nt':raise UpdateBlocked('正式安装仅在 Windows 执行')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80000000,0x1,None,3,0x80,None)
    if handle in (None,wintypes.HANDLE(-1).value):raise ctypes.WinError(ctypes.get_last_error())
    try:yield
    finally:kernel.CloseHandle(handle)


def _launch_and_verify(executable,plan,version,build,label):
    directory=Path(plan['home'])/'updates/transactions'/plan['id']
    ready=directory/(label+'-ready.json');stop=directory/(label+'-shutdown')
    environment={key:value for key,value in os.environ.items() if not any(word in key.upper() for word in ('KEY','TOKEN','PASSWORD','SECRET'))}
    environment['STUDIO_UPDATE_TRANSACTION_ID']=plan['id']
    log=(directory/(label+'-startup.log')).open('w',encoding='utf8')
    process=subprocess.Popen([str(executable),'--data-home',plan['home'],'--ready-file',str(ready),
        '--shutdown-file',str(stop),'--skip-migration-prompt'],env=environment,stdin=subprocess.DEVNULL,
        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+180
    try:
        while time.monotonic()<deadline:
            if process.poll() is not None:raise UpdateRejected('更新后的真实桌面进程提前退出')
            if ready.exists():
                result=json.loads(ready.read_text('utf8'))
                if result.get('gui')!='edgechromium-loaded':time.sleep(.2);continue
                from urllib.parse import urlsplit
                parsed=urlsplit(result.get('url',''))
                if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or not parsed.port:
                    raise UpdateRejected('更新后的健康地址身份无效')
                with opener.open(result['url']+'/api/bootstrap-health',timeout=5) as response:
                    health=json.load(response)['data']
                if (health.get('version')!=version or health.get('build')!=build or health.get('pid')!=result.get('pid')
                        or Path(health.get('data_dir','')).resolve()!=(Path(plan['home'])/'data').resolve()):
                    raise UpdateRejected('更新后版本或数据目录身份不匹配')
                return {'pid':process.pid,'backend_pid':health['pid'],'version':version,'build':build,'url':result['url']}
            time.sleep(.2)
        raise UpdateRejected('更新后的桌面健康检查超过三分钟')
    except Exception:
        stop.touch()
        try:process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            # Only this newly launched owned process is terminated. The GUI's
            # own job object closes its backend/Node/Chrome descendants.
            process.terminate();process.wait(timeout=10)
        raise
    finally:log.close()


def validate_runner(plan,actual_executable):
    runner=plain_path(plan['runner']);home=plain_path(plan['home'])
    expected=home/'updates/runners'/plan['id']/'SeedanceStudio.exe'
    if runner!=expected or plain_path(actual_executable)!=runner:
        raise UpdateRejected('更新 helper 必须从当前事务的独立受控副本运行')
    files=plan.get('runner_files')
    if not isinstance(files,list) or not files or len(files)>100000:
        raise UpdateRejected('helper 文件清单缺失或过大')
    names=[item.get('path') for item in files if isinstance(item,dict)]
    if (len(names)!=len(files) or any(not isinstance(name,str) for name in names)
            or len(set(names))!=len(names) or 'SeedanceStudio.exe' not in names):
        raise UpdateRejected('helper 文件清单缺少主程序或含重复条目')
    for item in plan['runner_files']:
        relative=PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in item['path'] or ':' in item['path']:
            raise UpdateRejected('helper 文件清单路径越界')
        file=plain_path(runner.parent.joinpath(*relative.parts))
        if not file.is_relative_to(runner.parent) or not file.is_file() or file.stat().st_size!=item['size'] or sha256(_native(file))!=item['sha256']:
            raise UpdateRejected('独立 helper 文件不完整或已被替换')


def _restore_program(plan):
    root=plain_path(plan.get('program_root',plan['install_root']));runner=plain_path(plan['runner']).parent
    if plan.get('versioned_layout') and all(
        (root/item['path']).is_file() and sha256(_native(root/item['path']))==item['sha256']
        for item in plan['runner_files']):
        return None  # Side-by-side installation kept the old payload intact.
    preserved=root.with_name(root.name+'.failed-update-'+plan['id'][:8])
    if preserved.exists():raise UpdateBlocked('失败版本保留目录已存在，需要核实恢复进度')
    root.rename(preserved)
    try:
        root.mkdir()
        for item in plan['runner_files']:
            relative=PurePosixPath(item['path']);source=runner.joinpath(*relative.parts);destination=root.joinpath(*relative.parts)
            _native(destination.parent).mkdir(parents=True,exist_ok=True)
            shutil.copy2(_native(source),_native(destination))
            if sha256(_native(destination))!=item['sha256']:raise UpdateRejected('旧版程序恢复哈希不匹配')
    except Exception:
        # Do not delete a partly restored tree or overwrite the preserved new
        # version. Keep the durable gate and all data for explicit recovery.
        raise UpdateBlocked('旧版程序恢复中断；已保留失败版本和独立副本，禁止直接开启不兼容数据库') from None
    return str(preserved)


def _restore_shortcuts(plan):
    """Retarget only this install's per-user shortcuts, preserving prior bytes."""
    if not plan.get('versioned_layout'):return
    executable=plain_path(Path(plan['program_root'])/'SeedanceStudio.exe')
    root=plain_path(plan['install_root'])
    backup=plain_path(Path(plan['home'])/'updates/transactions'/plan['id'])
    literal=lambda value:"'"+str(value).replace("'","''")+"'"
    script=("$ErrorActionPreference='Stop';$shell=New-Object -ComObject WScript.Shell;"
        "$root="+literal(root)+";$exe="+literal(executable)+";$backup="+literal(backup)+";"
        "$folders=@([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'));"
        "$index=0;foreach($folder in $folders){"
        "$path=Join-Path $folder 'Seedance Studio.lnk';"
        "if((Test-Path -LiteralPath $path) -or $index -eq 0){"
        "$shortcut=$shell.CreateShortcut($path);"
        "if(Test-Path -LiteralPath $path){"
        "$target=[IO.Path]::GetFullPath($shortcut.TargetPath);"
        "if(!$target.StartsWith($root+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Unrelated shortcut preserved; manual review required'};"
        "$copy=Join-Path $backup ('shortcut-'+$index+'.lnk');"
        "if(Test-Path -LiteralPath $copy){throw 'Shortcut backup already exists'};"
        "Copy-Item -LiteralPath $path -Destination $copy};"
        "$shortcut.TargetPath=$exe;$shortcut.WorkingDirectory=[IO.Path]::GetDirectoryName($exe);$shortcut.Save()};$index++}")
    process=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',
        base64.b64encode(script.encode('utf-16le')).decode('ascii')],capture_output=True,timeout=30,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if process.returncode:raise UpdateBlocked('旧版快捷方式恢复未通过，原快捷方式和程序已保留，需人工核实')


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args(argv)
    home=plain_path(os.environ.get('STUDIO_DATA_HOME',''))
    file=plain_path(args.plan)
    if file.parent!=home/'updates/plans' or not re.fullmatch(r'[0-9a-f]{32}\.json',file.name) or not file.is_file() or file.stat().st_size>16000000:
        raise UpdateRejected('更新计划必须位于当前用户的受控事务目录')
    plan=json.loads(file.read_text('utf8'))
    if (plan.get('format')!='seedance.update-plan@1' or plan.get('product_id')!=PRODUCT_ID
            or plan.get('id')!=file.stem or plan.get('state')!='prepared' or plain_path(plan.get('home',''))!=home):
        raise UpdateRejected('更新计划身份、阶段或产品无效')
    from core.runtime_layout import resource_root
    root=resource_root()
    service=UpdateService(home,root,current_version=_source_constant(root/'core/config.py','APP_VERSION'),
        current_build=_source_constant(root/'core/config.py','APP_BUILD'),current_schema=_source_constant(root/'core/db.py','CURRENT_SCHEMA_VERSION'))
    # Trust is checked before process manipulation, file replacement or install.
    service.require_policy();validate_runner(plan,sys.executable)
    release,package,signer=service.verifier.verify(plan['target_id'])
    if release.__dict__!=plan['release']:raise UpdateRejected('已签名更新与用户确认的事务不同')
    conn=sqlite3.connect(service.control_path,timeout=15)
    try:
        conn.execute('BEGIN IMMEDIATE');_gate_schema(conn)
        gate=conn.execute('SELECT transaction_id,state FROM studio_update_gate WHERE slot=1').fetchone()
        if gate!=(plan['id'],'prepared'):raise UpdateRejected('更新事务已被其他进程接管或不再有效')
        conn.execute("UPDATE studio_update_gate SET state='helper_claimed',updated=? WHERE slot=1",(time.time(),));conn.commit()
    finally:conn.close()
    installation_started=False
    try:
        _stop_owned_parent(plan)
        plan['state']='installing';atomic_json(file,plan)
        with _deny_package_replacement(package):
            verify_package(package,release);service.signature_verifier(package,service.require_policy().signer_sha256)
            installation_started=True
            directory=service.directory/'transactions'/plan['id']
            result=subprocess.run([str(package),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',
                '/DIR='+plan['install_root'],'/LOG='+str(directory/'installer.log')],timeout=600,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode!=0:raise UpdateRejected('更新安装器未成功完成，退出码 '+str(result.returncode))
        new_root=(Path(plan['install_root'])/'versions'/(release.version+'-'+release.build)
                  if plan.get('versioned_layout') else Path(plan['install_root']))
        health=_launch_and_verify(new_root/'SeedanceStudio.exe',plan,release.version,release.build,'new-version')
        plan.update(state='completed',health=health);atomic_json(file,plan)
        with sqlite3.connect(service.control_path) as conn:conn.execute('DELETE FROM studio_update_gate WHERE transaction_id=?',(plan['id'],))
        service.history_store.add('completed','真实新版窗口、版本及后台身份验证通过',plan['target_id'])
        return 0
    except Exception as exc:
        plan.update(state='recovery_required',error_type=type(exc).__name__);atomic_json(file,plan)
        if installation_started:
            try:
                data=service.recovery.restore(plan)
                preserved=_restore_program(plan)
                _restore_shortcuts(plan)
                health=_launch_and_verify(Path(plan.get('program_root',plan['install_root']))/'SeedanceStudio.exe',plan,plan['current_version'],plan['current_build'],'rollback-version')
                plan.update(state='rolled_back',recovery=data,preserved_failed_install=preserved,health=health,
                    installer_registration='May still describe attempted version; retained old program/data are verified, reinstallation may be required')
                atomic_json(file,plan)
                with sqlite3.connect(service.control_path) as conn:conn.execute('DELETE FROM studio_update_gate WHERE transaction_id=?',(plan['id'],))
            except Exception as recovery_error:
                plan['recovery_error_type']=type(recovery_error).__name__;atomic_json(file,plan)
        else:
            # No installer ran: releasing the gate cannot roll back user writes.
            plan['state']='cancelled_before_install';atomic_json(file,plan)
            with sqlite3.connect(service.control_path) as conn:conn.execute('DELETE FROM studio_update_gate WHERE transaction_id=?',(plan['id'],))
        service.history_store.add('failed',type(exc).__name__,plan['target_id'])
        return 1


if __name__=='__main__':raise SystemExit(main())
