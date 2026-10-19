"""Atomic offline installation of checksum-bound runtimes into short paths."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):result.update(chunk)
    return result.hexdigest()


def extract_verified(archive,destination,expected):
    archive,destination=Path(archive),Path(destination)
    if not re.fullmatch('[0-9a-f]{64}',expected) or digest(archive)!=expected:
        raise RuntimeError('Bundled runtime checksum mismatch; re-extract original installation ZIP')
    parent=destination.parent
    parent.mkdir(parents=True,exist_ok=True)
    # No links anywhere in the managed runtime ancestry.
    for part in (parent,*parent.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise PermissionError('Runtime installation target cannot contain directory links')
    marker=destination/'.seedance-runtime.json'
    if destination.exists():
        if destination.is_symlink() or (hasattr(destination,'is_junction') and destination.is_junction()):
            raise PermissionError('Runtime directory cannot be a link')
        if destination.is_dir() and marker.is_file():
            installed=json.loads(marker.read_text())
            files=installed.get('files')
            if installed.get('sha256')==expected and isinstance(files,dict) and files:
                for name,checksum in files.items():
                    file=destination/name
                    if (not file.resolve().is_relative_to(destination.resolve()) or file.is_symlink()
                        or not file.is_file() or digest(file)!=checksum):
                        raise RuntimeError('Installed runtime changed or is incomplete; files preserved for diagnosis')
                return 'reused'
        raise RuntimeError('Existing runtime directory is incomplete; preserved for diagnosis')
    temporary=Path(tempfile.mkdtemp(prefix='.install-',dir=parent))
    try:
        with zipfile.ZipFile(archive) as bundle:
            names=set();total=0;files={}
            for info in bundle.infolist():
                name=info.filename;parts=PurePosixPath(name).parts
                mode=info.external_attr>>16
                if (not parts or info.orig_filename!=name or '\x00' in info.orig_filename
                    or name.startswith('/') or '\\' in name or ':' in name
                    or any(p in {'.','..'} or p.rstrip(' .')!=p or
                        p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for p in parts)
                    or stat.S_ISLNK(mode) or name.casefold().rstrip('/') in names):
                    raise RuntimeError('Unsafe or duplicate bundled runtime entry')
                names.add(name.casefold().rstrip('/'));total+=info.file_size
                if total>2*1024**3 or info.file_size>600*1024**2:raise RuntimeError('Bundled runtime exceeds extraction bounds')
                target=temporary.joinpath(*parts)
                if not target.resolve().is_relative_to(temporary.resolve()):raise RuntimeError('Runtime entry escapes staging')
                if info.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                target.parent.mkdir(parents=True,exist_ok=True)
                # Explicit long-path prefix also handles unusually long npm filenames.
                output=str(target.resolve())
                if os.name=='nt':output='\\\\?\\'+output
                with bundle.open(info) as incoming,open(output,'xb') as outgoing:
                    checksum=hashlib.sha256()
                    while chunk:=incoming.read(1024*1024):
                        checksum.update(chunk);outgoing.write(chunk)
                    files[name]=checksum.hexdigest()
            (temporary/'.seedance-runtime.json').write_text(json.dumps({'sha256':expected,'files':files}),encoding='utf8')
        temporary.rename(destination)
        return 'installed'
    finally:
        if temporary.exists():
            if temporary.resolve().parent!=parent.resolve() or not temporary.name.startswith('.install-'):
                raise PermissionError('Unsafe staging cleanup target')
            shutil.rmtree(temporary)


def install():
    manifest=ROOT/'tools/hypit_bundle.json'
    if not manifest.exists():return {'status':'development tree; no extraction required'}
    doc=json.loads(manifest.read_text(encoding='utf8'))
    if doc.get('format')!='seedance.hypit-bundle@1':raise RuntimeError('Unknown runtime bundle format')
    base=Path(os.environ['LOCALAPPDATA'])/'SeedanceStudio/assets/hypit-host'
    results={}
    for name in ('sdk','browser'):
        item=doc[name]
        if item.get('file')!=name+'-payload.zip':raise RuntimeError('Unexpected runtime payload name')
        target=base/(name+'-'+item['sha256'][:12])
        results[name]=extract_verified(ROOT/'tools'/item['file'],target,item['sha256'])
    return results


if __name__=='__main__':print(json.dumps(install()))
