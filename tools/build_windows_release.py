"""Rebuild a full Windows candidate from this Git repository, not an old ZIP.

Run npm ci/build:offline first. Only public, checksum-pinned binaries are fetched.
Does not install Studio, load account keys, call models, publish or stamp stable.
"""
import argparse,ast,hashlib,json,os,re,shutil,stat,subprocess,sys,tempfile,time,uuid,zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit
from build_hypit_payload import build_payload
from trusted_download import open_public_url
from install_hypit_bundle import native_path

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN={'.git','.venv','.env','data','install_logs','__pycache__','node_modules','.pytest_cache'}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

def fetch(item,cache):
    validate_runtime_url(item['url'])
    if urlsplit(item['url']).scheme!='https' or not re.fullmatch('[a-f0-9]{64}',item['sha256']):
        raise ValueError('Pinned public HTTPS archive required')
    target=cache/urlsplit(item['url']).path.rsplit('/',1)[1]
    if target.exists():
        if sha(target)!=item['sha256']:raise ValueError('Cache checksum mismatch; existing file preserved')
        return target
    if item.get('bytes'):
        # Same upstream hash, reviewed domestic mirrors first. No version selection.
        sys.path.insert(0,str(ROOT))
        from core.component_download import fetch as fetch_component
        last=[0]
        def progress(**value):
            if value['downloaded']-last[0]>=8*1024**2:
                print(target.name+': '+str(value['downloaded']//1024**2)+' MiB · '+value['source'],flush=True)
                last[0]=value['downloaded']
        return fetch_component(item,cache/'verified-components',progress=progress)
    partial=target.with_name(target.name+'.'+uuid.uuid4().hex+'.part')
    start=time.monotonic();total=0;last=0
    with open_public_url(item['url'],timeout=35,validate_url=validate_runtime_url) as response,partial.open('xb') as out:
        if urlsplit(response.geturl()).scheme!='https':raise ValueError('Insecure download redirect')
        while block:=response.read(1024*1024):
            total+=len(block)
            if total>600*1024**2 or time.monotonic()-start>900:raise TimeoutError('Public download bound exceeded')
            out.write(block)
            if total-last>=8*1024**2:
                print(target.name+': '+str(total//1024**2)+' MiB',flush=True);last=total
    if sha(partial)!=item['sha256']:raise ValueError('Download checksum mismatch; partial preserved')
    partial.rename(target)
    return target


def validate_runtime_url(url):
    patterns=(r'https://nodejs\.org/dist/v(\d+\.\d+\.\d+)/node-v\1-win-x64\.zip',
              r'https://storage\.googleapis\.com/chrome-for-testing-public/\d+\.\d+\.\d+\.\d+/win64/chrome-headless-shell-win64\.zip',
              r'https://www\.gyan\.dev/ffmpeg/builds/packages/ffmpeg-\d+\.\d+(?:\.\d+)?-essentials_build\.zip',
              r'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/[0-9a-f-]{36}/Microsoft\.WebView2\.FixedVersionRuntime\.\d+\.\d+\.\d+\.\d+\.x64\.cab')
    if not isinstance(url,str) or not any(re.fullmatch(pattern,url) for pattern in patterns):
        raise ValueError('Untrusted runtime download source; explicit source review required')

def extract(archive,target):
    """Validate the complete archive before any write; no links/ADS/traversal."""
    target=native_path(target)
    with zipfile.ZipFile(archive) as bundle:
        seen=set();total=0
        for item in bundle.infolist():
            parts=PurePosixPath(item.filename).parts
            if (not parts or item.filename.startswith('/') or '\\' in item.filename or ':' in item.filename
                or item.orig_filename!=item.filename or any(p in {'.','..'} or p.rstrip(' .')!=p or
                p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for p in parts)
                or stat.S_ISLNK(item.external_attr>>16) or item.filename.casefold() in seen):
                raise ValueError('Unsafe public archive')
            seen.add(item.filename.casefold());total+=item.file_size
            if total>2*1024**3 or item.file_size>600*1024**2:raise ValueError('Archive extraction bound exceeded')
        for item in bundle.infolist():
            path=target.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():path.mkdir(parents=True,exist_ok=True);continue
            path.parent.mkdir(parents=True,exist_ok=True)
            with bundle.open(item) as src,path.open('xb') as dst:shutil.copyfileobj(src,dst)

def tracked_inputs(root):
    git=shutil.which('git')
    if not git:raise RuntimeError('Git is required; run from a cloned repository')
    raw=subprocess.check_output([git,'-C',str(root),'ls-files','-z'])
    names=[x.decode('utf8') for x in raw.split(b'\0') if x]
    if not names:raise ValueError('No tracked source inputs')
    for name in names:
        parts=PurePosixPath(name).parts
        if any(p in FORBIDDEN for p in parts) or Path(name).suffix.lower() in {'.db','.sqlite','.dpapi','.key','.pem','.log','.pid'}:
            raise PermissionError('Private/runtime file is tracked: '+name)
        if (root/name).is_symlink():raise PermissionError('Source links are not packaged')
    return names

def versions(root):
    values={}
    for node in ast.parse((root/'core/config.py').read_text('utf-8-sig')).body:
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in {'APP_VERSION','APP_BUILD'}:values[target.id]=node.value.value
    if len(values)!=2:raise ValueError('Static application version metadata required')
    return values

def build(output,cache):
    if os.name!='nt':raise RuntimeError('Build Windows native npm payloads on Windows x64; Cloud can run offline source tests on Linux')
    if os.getenv('PROCESSOR_ARCHITEW6432',os.getenv('PROCESSOR_ARCHITECTURE'))!='AMD64':raise RuntimeError('Windows x64 required')
    output=output.resolve();cache=cache.resolve()
    if output.exists() or output.with_suffix('.zip').exists():raise FileExistsError('Previous builds preserved; choose a new output')
    inputs=tracked_inputs(ROOT);identity={name:sha(ROOT/name) for name in inputs}
    lock=json.loads((ROOT/'tools/windows-runtime.lock.json').read_text('utf8'))
    if lock.get('format')!='seedance.windows-build-lock@1':raise ValueError('Unknown runtime lock')
    cache.mkdir(parents=True,exist_ok=True)
    archives={name:fetch(lock[name],cache) for name in ('node','browser','ffmpeg')}
    sdk=ROOT/'tools/hypit_provider'
    if not (sdk/'node_modules/@seedance-studio/openlux-provider/dist/src/activation.js').is_file():
        raise RuntimeError('Run npm ci and npm run build:offline in tools/hypit_provider first')
    # Require the installed dependency graph to match the lock; no hidden parent SDK.
    installed=json.loads((sdk/'node_modules/.package-lock.json').read_text('utf8'))
    declared=json.loads((sdk/'package-lock.json').read_text('utf8'))
    for name,row in installed.get('packages',{}).items():
        if name.startswith('node_modules/@seedance-studio/'):continue
        if name not in declared['packages'] or row.get('version')!=declared['packages'][name].get('version'):
            raise ValueError('Installed npm tree differs from repository lock: '+name)
    output.mkdir(parents=True);app=output/'Studio';app.mkdir()
    for name in inputs:
        target=app/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
    # Keep runtime paths short, and still support extended Windows file paths.
    # Never derive the unpack path from an arbitrarily long release title.
    scratch=Path(tempfile.mkdtemp(prefix='stage-',dir=cache))
    extract(archives['node'],scratch/'node')
    nodebase=scratch/'node'/lock['node']['archive_root']
    (app/'tools/node').mkdir(exist_ok=True)
    shutil.copy2(nodebase/'node.exe',app/'tools/node/node.exe')
    # Preserve the versioned license byte-for-byte; keep the exact downloaded
    # runtime license alongside it rather than silently overwriting Git input.
    shutil.copy2(nodebase/'LICENSE',app/'tools/node/NODE_UPSTREAM_LICENSE.txt')
    browser=scratch/'browser';extract(archives['browser'],browser/lock['browser']['cache_root'])
    sdk_item=build_payload(sdk,app/'tools/sdk-payload.zip')
    browser_item=build_payload(native_path(browser),app/'tools/browser-payload.zip')
    write(app/'tools/hypit_bundle.json',{'format':'seedance.hypit-bundle@1','sdk':sdk_item,'browser':browser_item})
    (app/'installers').mkdir(exist_ok=True);shutil.copy2(archives['ffmpeg'],app/'installers'/lock['ffmpeg']['file'])
    acceptance=ROOT/'tools/acceptance'
    shutil.copytree(acceptance,output/'tools')
    for name in ('01_START_ACCEPTANCE.cmd','02_AFTER_RESTART.cmd'):
        shutil.copy2(acceptance/name,output/name)
    shutil.copy2(acceptance/'README.txt',output/'README_ACCEPTANCE.txt')
    proof={'format':'seedance.repository-build@1','version':versions(ROOT),'tracked_source_hashes':identity,
        'runtime_lock':lock,'fresh_windows_passed':False,'release_passed':False,'candidate_only':True,'paid_calls':0}
    write(app/'docs/REPOSITORY_BUILD.json',proof)
    # Rebuild source files and inputs must remain exactly the snapshot hashed above.
    if any(sha(ROOT/name)!=value for name,value in identity.items()):raise RuntimeError('Source changed during build')
    from repository_safety import scan_tree
    safety=scan_tree(output)
    write(app/'docs/REPOSITORY_SAFETY.json',safety)
    if safety['findings']:raise PermissionError('Candidate safety scan failed; do not distribute')
    manifest=app/'PACKAGE_FILE_MANIFEST.txt'
    manifest.write_text(''.join(sha(p)+'  '+p.relative_to(app).as_posix()+'\n' for p in sorted(app.rglob('*')) if p.is_file() and p!=manifest),encoding='utf8')
    write(output/'payload-sha256.json',[{'path':p.relative_to(app).as_posix(),'sha256':sha(p)} for p in sorted(app.rglob('*')) if p.is_file()])
    archive=output.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for p in sorted(output.rglob('*')):
            if p.is_file():bundle.write(p,output.name+'/'+p.relative_to(output).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():raise ValueError('Candidate CRC verification failed')
        for name,value in identity.items():
            if hashlib.sha256(bundle.read(output.name+'/Studio/'+name)).hexdigest()!=value:
                raise ValueError('Git source/archive mismatch: '+name)
    receipt={'archive':archive.name,'sha256':sha(archive),'bytes':archive.stat().st_size,
        'source_files':len(identity),'every_source_hash_verified':True,'candidate_only':True,
        'fresh_windows_passed':False,'release_passed':False,'paid_calls':0}
    write(archive.with_suffix('.receipt.json'),receipt)
    archive.with_suffix('.zip.sha256').write_text(receipt['sha256']+'  '+archive.name+'\n',encoding='utf8')
    print(json.dumps(receipt),flush=True)
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cache',type=Path,default=ROOT/'build/download-cache')
    args=parser.parse_args();build(args.output,args.cache)
