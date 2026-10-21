"""Crash-recoverable activation of verified, health-checked component directories."""
import json
import os
import re
import uuid
import time


def _rename(source,target):
    # Windows can retain image/scan handles briefly after a verified child exits.
    # Retry only sharing/access errors, under the writer lease, for at most 5 s.
    deadline=time.monotonic()+5
    retries=0
    while True:
        try:source.rename(target);return retries
        except OSError as exc:
            if getattr(exc,'winerror',None) not in {5,32,33} or time.monotonic()>=deadline or retries>=20:
                raise
            if target.exists():raise
            time.sleep(min(.05*2**min(retries,4),.5,max(0,deadline-time.monotonic())))
            retries+=1


def _write(path, value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as stream:
            json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def recover(destination, item):
    """Caller holds the component writer lease. Never remove preserved bytes."""
    from .component_manager import plain,identity,validate_installed
    destination=plain(destination)
    journal=plain(destination.with_name(destination.name+'.activation.json'))
    if not journal.is_file():return
    value=json.loads(journal.read_text('utf8'))
    if (value.get('identity')!=identity(item)
        or not re.fullmatch(re.escape(destination.name)+r'\.preserved-[a-f0-9]{32}',value.get('backup',''))):
        raise PermissionError('组件恢复记录无效，原文件已保留')
    backup=plain(destination.with_name(value['backup']))
    if destination.exists() and validate_installed(destination,item):
        journal.unlink();return
    if backup.is_dir():
        if destination.exists():
            _rename(destination,destination.with_name(destination.name+'.rejected-'+uuid.uuid4().hex))
        _rename(backup,destination)
    elif value.get('had_destination'):
        raise RuntimeError('组件回滚原目录缺失，已停止覆盖；请保留目录并修复')
    journal.unlink()


def activate(staging,destination,item):
    from .component_manager import plain,identity,validate_installed
    staging,destination=plain(staging),plain(destination)
    recover(destination,item)
    backup=destination.with_name(destination.name+'.preserved-'+uuid.uuid4().hex)
    journal=destination.with_name(destination.name+'.activation.json')
    value={'identity':identity(item),'backup':backup.name,'had_destination':destination.exists()}
    # Persist the intended backup name before either rename; recovery also works
    # when the process dies between the renames or before saving final state.
    _write(journal,value)
    try:
        retries=0
        if value['had_destination']:retries+=_rename(destination,backup)
        retries+=_rename(staging,destination)
        if not validate_installed(destination,item):raise PermissionError('组件切换后校验失败')
        journal.unlink()
        return {'rename_retries':retries}
    except Exception:
        if backup.exists():
            if destination.exists():_rename(destination,destination.with_name(destination.name+'.rejected-'+uuid.uuid4().hex))
            _rename(backup,destination)
        elif not value['had_destination'] and destination.exists():
            _rename(destination,destination.with_name(destination.name+'.rejected-'+uuid.uuid4().hex))
        journal.unlink(missing_ok=True)
        raise
