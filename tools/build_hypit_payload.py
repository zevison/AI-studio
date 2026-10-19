"""Package compiled SDK code, excluding generated caches and local runtime state."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile

EXCLUDED={'.git','.cache','.vite','__pycache__','.hypit','.hypit-html-browser-program','.env'}


def build_payload(sdk_root,output):
    root=Path(sdk_root).resolve();output=Path(output)
    if not root.is_dir():raise ValueError('SDK source directory not found')
    output.parent.mkdir(parents=True,exist_ok=True)
    handle,path=tempfile.mkstemp(prefix='.sdk-payload-',suffix='.tmp',dir=output.parent)
    os.close(handle);temporary=Path(path)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for file in sorted(root.rglob('*')):
                relative=file.relative_to(root)
                if any(part in EXCLUDED for part in relative.parts):continue
                if file.suffix in {'.log','.pid','.pyc','.zip'}:continue
                if file.is_symlink() or (hasattr(file,'is_junction') and file.is_junction()):raise PermissionError('SDK payload cannot contain links')
                if not file.is_file():continue
                before=file.stat();archive.write(file,relative.as_posix());after=file.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('SDK changed during packaging')
        checksum=hashlib.sha256()
        with temporary.open('rb') as stream:
            while chunk:=stream.read(1024*1024):checksum.update(chunk)
        os.replace(temporary,output)
        return {'file':output.name,'sha256':checksum.hexdigest(),'bytes':output.stat().st_size}
    finally:temporary.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--sdk-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args()
    import json
    print(json.dumps(build_payload(options.sdk_root,options.output)))
