"""Recreate a reviewed Windows wheelhouse from public pinned sources.

Does not install application packages into the invoking Python. One upstream
sdist is built in a disposable venv and must reproduce its recorded wheel SHA.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from trusted_download import open_public_url

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def download(record,directory,source=False):
    url=record['url'];parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.hostname!='files.pythonhosted.org' or parsed.username
            or parsed.password or parsed.port not in (None,443) or parsed.query or parsed.fragment):
        raise ValueError('Only exact public PyPI artifacts are accepted')
    name=record['source_filename'] if source else record['filename']
    checksum=record['source_sha256'] if source else record['sha256']
    if (not re.fullmatch(r'[A-Za-z0-9_.+-]+',name) or not name.endswith(('.whl','.tar.gz'))
            or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL'}
            or not re.fullmatch('[a-f0-9]{64}',checksum)):
        raise ValueError('Invalid locked artifact identity')
    target=directory/name
    if target.is_symlink() or getattr(target,'is_junction',lambda:False)():
        raise ValueError('Artifact target cannot be a link')
    if target.exists():
        if digest(target)!=checksum:raise ValueError('Cached artifact was changed; original preserved')
        return target
    partial=target.with_suffix(target.suffix+'.part')
    if partial.exists():raise FileExistsError('Incomplete download preserved: '+str(partial))
    started=time.monotonic();size=0
    def validate(value):
        if value!=url:raise ValueError('Artifact redirect is outside its exact reviewed URL')
    with open_public_url(url,timeout=40,validate_url=validate) as incoming,partial.open('xb') as outgoing:
        while chunk:=incoming.read(1024*1024):
            size+=len(chunk)
            if size>500*1024**2 or time.monotonic()-started>600:raise TimeoutError('Artifact download exceeded bounds')
            outgoing.write(chunk)
    if digest(partial)!=checksum:raise ValueError('Downloaded artifact SHA mismatch; partial preserved')
    partial.rename(target)
    return target


def prepare(lock,directory):
    document=json.loads(Path(lock).read_text('utf8'))
    if document.get('complete') is not True or sys.version.split()[0]!=document['python']:
        raise ValueError('Select the reviewed Python runtime before preparing its wheels')
    directory=Path(directory).absolute()
    if directory.is_symlink() or any(getattr(p,'is_junction',lambda:False)() for p in (directory,*directory.parents)):
        raise ValueError('Wheelhouse cannot be a linked directory')
    directory.mkdir(parents=True,exist_ok=True)
    directory=directory.resolve()
    records=document['packages'];local=[]
    for index,record in enumerate(records,1):
        print(str(index)+'/'+str(len(records))+' '+record['name']+' '+record['version'],flush=True)
        if record['provenance']=='official-pypi-wheel':download(record,directory)
        elif record['provenance']=='locally-built-wheel-from-verified-official-sdist':local.append(record)
        else:raise ValueError('Unknown package provenance')
    for record in local:
        target=directory/record['filename']
        if target.exists() and digest(target)==record['sha256']:continue
        if target.exists():raise ValueError('Local wheel checksum mismatch; do not overwrite it')
        source=download(record,directory,source=True)
        # pip's nested wheel cache can exceed MAX_PATH when the repository
        # lives below a long Chinese/spaced path. Keep only this owned scratch
        # venv in the OS temp directory and disable pip's nested cache.
        with tempfile.TemporaryDirectory(prefix='studio-wheel-') as temporary:
            work=Path(temporary);environment=dict(os.environ)
            for key in list(environment):
                if any(secret in key.upper() for secret in ('TOKEN','SECRET','PASSWORD','KEY')):environment.pop(key)
            environment.update(SOURCE_DATE_EPOCH=record['builder']['SOURCE_DATE_EPOCH'],PIP_NO_INDEX='1',
                HOME=str(work),USERPROFILE=str(work),PIP_CONFIG_FILE=os.devnull,PYTHONUTF8='1')
            subprocess.run([sys.executable,'-m','venv',str(work/'venv')],check=True,timeout=90,env=environment)
            python=work/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            tools=[next(row for row in records if row['name']==name) for name in ('pip','setuptools')]
            subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps',
                *[str(directory/row['filename']) for row in tools]],check=True,timeout=90,env=environment)
            subprocess.run([str(python),'-m','pip','wheel','--no-cache-dir','--no-deps','--no-build-isolation','--no-index',
                '--wheel-dir',str(directory),str(source)],check=True,timeout=90,env=environment)
        if digest(target)!=record['sha256']:
            raise ValueError('Source-built wheel did not reproduce the reviewed hash; no application install performed')
    receipt={'format':'seedance.wheelhouse@1','python':document['python'],'packages':len(records),
        'files':{row['filename']:digest(directory/row['filename']) for row in records}}
    (directory/'verification.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock',type=Path,default=ROOT/'requirements/python-win-x64-py314-r39-candidate.json')
    parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args();prepare(options.lock,options.output)
