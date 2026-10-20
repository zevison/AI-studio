"""Reproducible complete candidate from a checksum-pinned binary baseline + sources.

No provider access, installation, production deployment or stable release stamping.
Prepare -> credential scan -> finalize; any payload change invalidates the scan.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

PRIVATE={'.git','.venv','.env','data','__pycache__','install_logs','node_modules'}
SOURCE_EXT={'.py','.js','.mjs','.cjs','.ts','.css','.html','.ps1','.cmd','.bat','.vbs','.txt','.md','.json','.svg','.woff','.woff2','.png','.tzif','.svml','.svs','.svrun','.mp4'}
GENERATED={'docs/SOURCE_PROVENANCE.json','docs/FRESH_PC_RELEASE_GATE.json','docs/CREDENTIAL_SCAN.json',
           'VERSION_REGISTER.json','PACKAGE_FILE_MANIFEST.txt'}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def tree_hash(packet):
    h=hashlib.sha256()
    for p in sorted(packet.rglob('*')):
        if not p.is_file() or p.name in {'CREDENTIAL_SCAN.json','PACKAGE_FILE_MANIFEST.txt','payload-sha256.json'}:continue
        h.update(p.relative_to(packet).as_posix().encode()+b'\0'+sha(p).encode()+b'\n')
    return h.hexdigest()


def source_hash(source):
    h=hashlib.sha256()
    paths=[source/'app.py',source/'00_START.cmd',source/'01_INSTALL.cmd',source/'requirements.txt']
    for name in ('core','static','tools','tests_integrated'):paths.extend((source/name).rglob('*'))
    excluded=PRIVATE|{'node','hypit_browser','runtime','vendor'}
    for p in sorted(set(paths)):
        if not p.is_file() or any(x in excluded for x in p.relative_to(source).parts) or p.suffix.lower() not in SOURCE_EXT:continue
        h.update(p.relative_to(source).as_posix().encode()+b'\0'+sha(p).encode()+b'\n')
    return h.hexdigest()


def evidence(folder,source):
    identity=json.loads((folder/'regression-source.json').read_text('utf8'))
    if identity.get('source_sha256')!=source_hash(source):raise ValueError('Tests belong to a different source tree')
    text=(folder/'full-regression-2.log').read_text('utf-8-sig')
    match=re.search(r'(\d+) passed(?:, (\d+) skipped)? in ',text)
    if not match or re.search(r'\b(?:FAILED|ERROR|[1-9]\d* failed)\b',text):raise ValueError('Current Python regression did not pass')
    result={'python_passed':int(match[1]),'python_skipped':int(match[2] or 0)}
    for name,key in [('frontend.log','frontend_passed'),('official-sdk-tests.log','sdk_passed')]:
        content=(folder/name).read_text('utf-8-sig');passed=re.search(r'# pass (\d+)',content)
        if not passed or '# fail 0' not in content:raise ValueError('Missing successful '+key)
        result[key]=int(passed[1])
    runtime=json.loads((folder/'http-runtime-acceptance.json').read_text('utf8'))
    if runtime.get('paid_calls')!=0 or not runtime.get('official_runtime',{}).get('local_runtime'):
        raise ValueError('Real local Runtime acceptance is missing')
    if runtime.get('source_sha256')!=source_hash(source):raise ValueError('HTTP/Runtime acceptance belongs to another source tree')
    return result


def prepare(source,baseline,baseline_sha,packet,revision,proof):
    if packet.exists() or packet.with_suffix('.zip').exists():raise FileExistsError('Preserve previously built packages')
    if not re.fullmatch(r'R\d+\.\d+',revision):raise ValueError('Explicit candidate revision required')
    if sha(baseline)!=baseline_sha:raise ValueError('Binary baseline checksum mismatch')
    checks=evidence(proof,source)
    # Validate every path before extracting anything. Never use extractall.
    with zipfile.ZipFile(baseline) as archive:
        members=archive.infolist();seen=set()
        if sum(x.file_size for x in members)>4*1024**3:raise ValueError('Unexpected baseline size')
        for item in members:
            parts=PurePosixPath(item.filename).parts
            if (len(parts)<2 or '\\' in item.filename or item.filename.startswith('/') or
                any(x in {'.','..'} or ':' in x or x in PRIVATE for x in parts) or
                stat.S_ISLNK(item.external_attr>>16) or item.filename.casefold() in seen):
                raise ValueError('Unsafe binary baseline')
            seen.add(item.filename.casefold())
        packet.mkdir(parents=True)
        for item in members:
            target=packet.joinpath(*PurePosixPath(item.filename).parts[1:])
            if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(item) as incoming,target.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing)
    app=packet/'Studio';updated={}
    # Never overlay changed provider sources while silently shipping an older
    # compressed SDK. Such changes require rebuilding the pinned binary payload.
    provider=source/'tools/hypit_provider'
    with zipfile.ZipFile(app/'tools/sdk-payload.zip') as sdk:
        for name in ('src','dist','scripts'):
            for p in (provider/name).rglob('*'):
                if not p.is_file() or p.suffix not in {'.ts','.js','.mjs','.json'}:continue
                relative=p.relative_to(provider).as_posix()
                if relative not in sdk.namelist() or hashlib.sha256(sdk.read(relative)).hexdigest()!=sha(p):
                    raise ValueError('SDK payload must be rebuilt: '+relative)
    roots=[source/'app.py',source/'00_START.cmd',source/'01_INSTALL.cmd',source/'requirements.txt']
    for name in ('core','static','tests_integrated','docs'):roots.extend((source/name).rglob('*'))
    roots.extend((source/'tools').glob('*'))
    for p in roots:
        if not p.is_file() or p.suffix.lower() not in SOURCE_EXT or '__pycache__' in p.parts:continue
        name=p.relative_to(source).as_posix()
        if name in GENERATED:continue
        target=app/name;target.parent.mkdir(parents=True,exist_ok=True)
        if p.suffix=='.py':ast.parse(p.read_text('utf-8-sig'),filename=name)
        shutil.copy2(p,target);updated[name]=sha(p)
    acceptance=source/'tools/acceptance'
    if not acceptance.is_dir():raise ValueError('Authoritative new-PC acceptance sources missing')
    shutil.copytree(acceptance,packet/'tools',dirs_exist_ok=True)
    for p in list(packet.glob('R3.*_*.txt')):
        target=app/'docs'/('previous_'+p.name)
        if target.exists():target=target.with_name('baseline_'+target.name)
        p.rename(target)
    report={'revision':revision,'status':'candidate_acceptance_pending','source_hashes':updated,
            'baseline_sha256':baseline_sha,'regression':checks,'fresh_windows_passed':False,
            'release_passed':False,'paid_calls':0,'production_deployed':False}
    write(app/'docs/SOURCE_PROVENANCE.json',report)
    write(app/'VERSION_REGISTER.json',{'assigned_release_version':'5.2.0','payload_revision':revision,
        'status':'candidate_clean_PC_acceptance_pending','fresh_pc_accepted':False,**checks})
    write(app/'docs/FRESH_PC_RELEASE_GATE.json',report)
    for p in proof.iterdir():
        if p.suffix in {'.json','.log','.txt'} and p.name not in {'inventory.json','progress.json'}:
            destination=app/'docs/audit-evidence'/p.name;destination.parent.mkdir(exist_ok=True);shutil.copy2(p,destination)
    write(app/'docs/CREDENTIAL_SCAN.json',{'passed':False,'reason':'Scan current complete candidate before finalize'})
    return report


def finalize(packet):
    app=packet/'Studio'
    report=json.loads((app/'docs/SOURCE_PROVENANCE.json').read_text('utf8'))
    scan=json.loads((app/'docs/CREDENTIAL_SCAN.json').read_text('utf8'))
    if not scan.get('passed') or scan.get('tree_sha256')!=tree_hash(packet):raise ValueError('Missing or stale whole-package credential scan')
    for name,expected in report['source_hashes'].items():
        if sha(app/name)!=expected:raise ValueError('Source provenance mismatch: '+name)
    manifest=app/'PACKAGE_FILE_MANIFEST.txt'
    manifest.write_text('# SHA256 candidate payload; clean Windows acceptance pending\n'+''.join(
        sha(p)+'  '+p.relative_to(app).as_posix()+'\n' for p in sorted(app.rglob('*')) if p.is_file() and p!=manifest),encoding='utf8')
    write(packet/'payload-sha256.json',[{'path':p.relative_to(app).as_posix(),'sha256':sha(p)} for p in sorted(app.rglob('*')) if p.is_file()])
    archive=packet.with_suffix('.zip')
    if archive.exists():raise FileExistsError('Never overwrite a delivered package')
    files=[]
    for p in sorted(packet.rglob('*')):
        if not p.is_file():continue
        if p.is_symlink() or any(x in PRIVATE for x in p.relative_to(packet).parts):raise PermissionError('Private package entry')
        files.append(p)
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in files:z.write(p,packet.name+'/'+p.relative_to(packet).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('Archive CRC failed')
        for p in files:
            h=hashlib.sha256()
            with z.open(packet.name+'/'+p.relative_to(packet).as_posix()) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
            if h.hexdigest()!=sha(p):raise ValueError('Archive member checksum mismatch')
    receipt={'archive':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size,'files':len(files),
             'candidate_only':True,'fresh_pc_accepted':False,'regression':report['regression'],
             'privacy_scan_passed':True,'crc_and_all_member_hashes_passed':True,'paid_calls':0}
    write(archive.with_suffix('.receipt.json'),receipt)
    archive.with_suffix('.zip.sha256').write_text(receipt['sha256']+'  '+archive.name+'\n',encoding='utf8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['prepare','finalize']);parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--source',type=Path);parser.add_argument('--baseline',type=Path)
    parser.add_argument('--baseline-sha');parser.add_argument('--revision');parser.add_argument('--evidence',type=Path)
    args=parser.parse_args()
    result=(prepare(args.source,args.baseline,args.baseline_sha,args.packet,args.revision,args.evidence)
            if args.phase=='prepare' else finalize(args.packet))
    print(json.dumps({k:v for k,v in result.items() if k!='source_hashes'},ensure_ascii=False))


if __name__=='__main__':main()
