"""Offline component containers. Embedded app catalog, never the ZIP, is authoritative."""
import hashlib
import json
from pathlib import Path
import stat
import uuid
import zipfile
from .component_download import digest,DownloadCancelled
from .runtime_layout import native_path


def _plain(path):
    path=native_path(path)
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents)):
        raise PermissionError('离线组件路径包含链接')
    return path


def pack_identity(item):
    # Byte identity is independent of mirrors, UI copy and future manifest additions.
    rows=[{'sha256':r['sha256'],'bytes':r['bytes'],'path':r.get('path','archive.zip')} for r in item['files']]
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def cache_artifact(path,item,cache):
    """Accept a browser-downloaded original only by embedded size and digest.

    The supplied filename is never used as an installation/cache path. This
    does not execute installers or accept a downloaded catalog as authority.
    """
    path,cache=_plain(path),_plain(cache)
    length=path.stat().st_size
    candidates=[r for r in item['files'] if r['bytes']==length]
    if not candidates:raise ValueError('下载文件大小不符，请使用本版本提供的链接')
    checksum=digest(path)
    if not any(r['sha256']==checksum for r in candidates):
        raise PermissionError('下载文件 SHA-256 不符，未安装；请重新下载正确版本')
    cache.mkdir(parents=True,exist_ok=True)
    target=_plain(cache/checksum)
    if target.exists():
        if digest(target)!=checksum:
            target.rename(target.with_name(target.name+'.rejected-'+uuid.uuid4().hex))
            path.rename(target)
        else:path.unlink()
    else:path.rename(target)
    return checksum


def import_pack(path,key,item,cache,*,cancelled=lambda:False,progress=lambda **_:None):
    path,cache=_plain(path),_plain(cache);cache.mkdir(parents=True,exist_ok=True)
    expected={'artifacts/'+row['sha256']:row for row in item['files']}
    if path.stat().st_size>sum(row['bytes'] for row in item['files'])+1024**2:
        raise ValueError('离线包大小超出受信任清单')
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        if (len(infos)!=len(expected)+1 or len({row.filename.casefold() for row in infos})!=len(infos)
            or set(row.filename for row in infos)!=set(expected)|{'manifest.json'}):
            raise PermissionError('离线包包含未知、重复或越界文件')
        for info in infos:
            if info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16) or info.is_dir():
                raise PermissionError('离线包包含加密、链接或目录条目')
            if info.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}:
                # LZMA/BZip2 headers can request large native decoder allocations
                # before the declared output-size limit has a chance to apply.
                raise PermissionError('离线包使用不支持的压缩算法，仅接受存储或 Deflate')
        metadata=archive.getinfo('manifest.json')
        if metadata.file_size>16384:raise ValueError('离线包清单过大')
        document=json.loads(archive.read(metadata))
        if (document.get('format')!='seedance.offline-component@1' or document.get('id')!=key
            or document.get('content_identity')!=pack_identity(item)):
            raise PermissionError('离线组件身份或版本与本程序受信任清单不符')
        paths=[];done=0
        for name,row in expected.items():
            info=archive.getinfo(name)
            if info.file_size!=row['bytes'] or info.file_size>800*1024**2:
                raise ValueError('离线组件文件大小不匹配')
            temporary=_plain(cache/(row['sha256']+'.import-'+uuid.uuid4().hex))
            checksum=hashlib.sha256();received=0
            try:
                with archive.open(info) as incoming,temporary.open('xb') as outgoing:
                    while block:=incoming.read(128*1024):
                        if cancelled():raise DownloadCancelled()
                        received+=len(block)
                        if received>row['bytes']:raise ValueError('离线组件解压超限')
                        checksum.update(block);outgoing.write(block)
                        progress(source='离线导入',downloaded=done+received,total=sum(r['bytes'] for r in item['files']))
                if received!=row['bytes'] or checksum.hexdigest()!=row['sha256']:
                    raise PermissionError('离线组件 SHA-256 校验失败，未安装')
                target=_plain(cache/row['sha256'])
                if target.exists():
                    if digest(target)!=row['sha256']:
                        target.rename(target.with_name(target.name+'.rejected-'+uuid.uuid4().hex))
                        temporary.rename(target)
                    else:temporary.unlink()
                else:temporary.rename(target)
                paths.append(target);done+=received
            finally:
                if temporary.exists():temporary.unlink()
    return [_plain(cache/row['sha256']) for row in item['files']]


def export_pack(path,key,item,cache,installed=None):
    path,cache=_plain(path),_plain(cache)
    if path.exists():raise FileExistsError('离线包已经存在，不覆盖原文件')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    try:
        with zipfile.ZipFile(temporary,'x',zipfile.ZIP_STORED) as archive:
            archive.writestr('manifest.json',json.dumps({'format':'seedance.offline-component@1',
                'id':key,'content_identity':pack_identity(item)},sort_keys=True))
            written=set()
            for row in item['files']:
                if row['sha256'] in written:continue
                source=_plain(cache/row['sha256'])
                if not source.is_file() and installed is not None and row.get('path'):
                    source=_plain(Path(installed)/row['path'])
                if not source.is_file() or source.stat().st_size!=row['bytes'] or digest(source)!=row['sha256']:
                    raise PermissionError('完整且已校验的原始组件尚未缓存，不能导出离线包')
                archive.write(source,'artifacts/'+row['sha256'])
                written.add(row['sha256'])
        temporary.rename(path)
    finally:
        if temporary.exists():temporary.unlink()
    return path
