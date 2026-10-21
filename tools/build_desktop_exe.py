"""Build a real per-user Windows installer from a captured, secret-scanned tree.

Requires requirements-desktop.txt, compiled npm provider, Inno Setup 6, and the
checksum-locked Microsoft-signed Fixed WebView2 CAB. No application API keys are accessed.
"""
from __future__ import annotations
import argparse
import ast
import base64
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from build_windows_release import fetch,extract
from build_hypit_payload import build_payload
from install_hypit_bundle import native_path
from repository_safety import scan_tree
from trusted_download import open_public_url
from export_build_sbom import export as export_sbom
from build_component_catalog import build_catalog

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN={'.git','.venv','.env','data','install_logs','__pycache__','node_modules','.pytest_cache','build','outputs'}
PREREQUISITE_SHA='90ae8eb0d9d22d6e51fccde49e4d25d266ff9343ad0cc931faa6f482569cea62'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def command(args,log,timeout=1800,env=None):
    print('Running '+str(args[0]),flush=True)
    with Path(log).open('w',encoding='utf8') as stream:
        result=subprocess.run(list(map(str,args)),cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout,env=env)
    if result.returncode:raise RuntimeError('Command failed ('+str(result.returncode)+'); see '+str(log))


def source_inputs(git):
    safe='safe.directory='+str(ROOT).replace('\\','/')
    raw=subprocess.check_output([git,'-c',safe,'-C',str(ROOT),'ls-files','--cached','--others','--exclude-standard','-z'])
    paths=sorted({v.decode('utf8') for v in raw.split(b'\0') if v})
    for name in paths:
        path=ROOT/name
        if any(p in FORBIDDEN for p in Path(name).parts) or path.suffix.lower() in {'.db','.sqlite','.dpapi','.key','.pem','.log','.pid'}:
            raise PermissionError('Unsafe source input: '+name)
        if not path.is_file() or path.is_symlink():raise PermissionError('Nonregular source input: '+name)
    return paths


def versions():
    out={}
    for node in ast.parse((ROOT/'core/config.py').read_text('utf-8-sig')).body:
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in {'APP_VERSION','APP_BUILD'}:out[target.id]=node.value.value
    if set(out)!={'APP_VERSION','APP_BUILD'}:raise RuntimeError('Application version not found')
    return out


def audit_payload(payload):
    """A public CA certificate store is not a secret PEM, verify exact wheel bytes."""
    result=scan_tree(payload);exemptions=[];remaining=[]
    for finding in result['findings']:
        if finding=={'path':'_internal/certifi/cacert.pem','kind':'private_path'}:
            distribution=importlib.metadata.distribution('certifi')
            record=next((p for p in distribution.files or [] if str(p).replace('\\','/')=='certifi/cacert.pem'),None)
            data=(payload/finding['path']).read_bytes()
            expected=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
            if (distribution.version=='2026.7.22' and record and record.hash and record.hash.mode=='sha256'
                and record.hash.value==expected and data==Path(distribution.locate_file(record)).read_bytes()
                and b'-----BEGIN CERTIFICATE-----' in data and b'PRIVATE KEY' not in data):
                exemptions.append(dict(finding,reason='Exact certifi 2026.7.22 wheel RECORD verified public CA certificate store',sha256=hashlib.sha256(data).hexdigest()))
                continue
        remaining.append(finding)
    for directory in ('data','_internal/data'):
        if (payload/directory).exists():
            remaining.append({'path':directory,'kind':'application_user_state'})
    result['findings']=remaining;result['verified_public_resource_exemptions']=exemptions
    return result


def payload_inventory(payload):
    files=[]
    for path in sorted(payload.rglob('*')):
        if path.is_symlink():raise PermissionError('Payload symlink is not allowed')
        if path.is_file():
            files.append({'path':path.relative_to(payload).as_posix(),'sha256':sha(path),'bytes':path.stat().st_size})
    return files


def verify_payload_unchanged(payload, before, report):
    after=payload_inventory(payload)
    if after!=before:
        old={v['path']:v for v in before};new={v['path']:v for v in after}
        write(report,{'status':'BLOCKED','added':sorted(new.keys()-old.keys()),
            'removed':sorted(old.keys()-new.keys()),
            'changed':sorted(k for k in old.keys() & new.keys() if old[k]!=new[k])})
        raise RuntimeError('Frozen payload changed during installer build; this installer is not a deliverable.')
    return after


def provision_download(item,cache):
    """Only the pinned OCR families or one immutable, public ASR repository."""
    patterns=(r'https://github\.com/ip7z/7zip/releases/download/\d+\.\d+/7z\d+-x64\.msi',
        r'https://github\.com/tesseract-ocr/tesseract/releases/download/[\d.]+/tesseract-ocr-w64-setup-[\d.]+\.exe',
        r'https://raw\.githubusercontent\.com/tesseract-ocr/tessdata_fast/[a-f0-9]{40}/(?:eng|chi_sim|osd)\.traineddata',
        r'https://raw\.githubusercontent\.com/tesseract-ocr/tessdata_fast/[a-f0-9]{40}/LICENSE',
        r'https://huggingface\.co/Systran/faster-whisper-small/resolve/[a-f0-9]{40}/(?:config\.json|model\.bin|tokenizer\.json|vocabulary\.(?:json|txt)|preprocessor_config\.json)',
        r'https://raw\.githubusercontent\.com/openai/whisper/[a-f0-9]{40}/LICENSE')
    if not any(re.fullmatch(p,item['url']) for p in patterns) or not re.fullmatch('[a-f0-9]{64}',item['sha256']):
        raise PermissionError('Unreviewed OCR/model resource URL or checksum')
    def allowed(url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):
            raise PermissionError('Invalid component download redirect')
        if url==item['url']:return
        if parsed.hostname in {'release-assets.githubusercontent.com','objects.githubusercontent.com'}:return
        if (parsed.hostname and (parsed.hostname.endswith('.hf.co') or parsed.hostname=='cdn-lfs.huggingface.co')
            and item['url'].startswith('https://huggingface.co/Systran/faster-whisper-small/')):return
        raise PermissionError('Component download redirected outside its approved release/CDN family')
    cache=cache/item['sha256'][:16];cache.mkdir(parents=True,exist_ok=True)
    destination=cache/urlsplit(item['url']).path.rsplit('/',1)[1]
    if destination.exists():
        if sha(destination)!=item['sha256']:raise PermissionError('Component cache checksum mismatch')
        return destination
    partial=destination.with_suffix(destination.suffix+'.part')
    if partial.exists():raise FileExistsError('A prior incomplete component download is preserved: '+str(partial))
    started=time.monotonic();size=0
    with open_public_url(item['url'],timeout=40,validate_url=allowed) as response,partial.open('xb') as outgoing:
        while block:=response.read(1024*1024):
            size+=len(block)
            if size>800*1024**2 or time.monotonic()-started>900:raise TimeoutError('Component download exceeded bounds')
            outgoing.write(block)
    if sha(partial)!=item['sha256']:raise PermissionError('Downloaded component checksum mismatch')
    partial.rename(destination);return destination


def provision_ocr(lock,cache,staging,resources,output,prepared=None):
    if not isinstance(lock.get('tesseract'),dict):raise RuntimeError('Complete OCR runtime lock required')
    item=lock['tesseract'];target=resources/'tools/tesseract'
    if prepared:
        original=Path(prepared)
    else:
        installer=provision_download(item,cache)
        extractor=provision_download(item['extractor'],cache)
        image=staging/'7zip-image'
        command(['msiexec.exe','/a',extractor,'/qn','TARGETDIR='+str(image),'/L*V',str(output/'7zip-admin.log')],output/'7zip-extract.log',timeout=120)
        candidates=list(image.rglob('7z.exe'))
        if len(candidates)!=1:raise RuntimeError('7-Zip administrative image missing executable')
        original=staging/'tesseract-extracted'
        command([candidates[0],'x',installer,'-o'+str(original),'-y'],output/'tesseract-extract.log',timeout=120)
    target.mkdir(parents=True)
    for path in original.iterdir():
        if path.is_file() and (path.suffix.lower()=='.dll' or path.name=='tesseract.exe'):shutil.copy2(path,target/path.name)
    for folder in ('tessdata','doc'):
        if (original/folder).is_dir():shutil.copytree(original/folder,target/folder)
    (target/'tessdata').mkdir(exist_ok=True)
    for language in item['languages']:
        if not re.fullmatch(r'(eng|chi_sim|osd)\.traineddata',language['file']):raise ValueError('Unexpected OCR language resource')
        source=provision_download(language,cache)
        shutil.copy2(source,target/'tessdata'/language['file'])
    (target/'doc').mkdir(exist_ok=True)
    shutil.copy2(provision_download(item['language_license'],cache),target/'doc/TESSDATA_LICENSE')
    if not (target/'tesseract.exe').is_file():raise RuntimeError('Portable OCR executable missing')
    # All files have derived provenance from the pinned release, not host PATH.
    write(target/'runtime-manifest.json',{'format':'seedance.tesseract-portable@1','provenance':item,
        'files':{p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}})


def provision_asr(lock,cache,resources,prepared=None):
    item=lock.get('asr')
    if not isinstance(item,dict) or not item.get('files'):raise RuntimeError('Complete small ASR model lock required')
    target=resources/'tools/runtime/models/faster-whisper-small';target.mkdir(parents=True)
    for row in item['files']:
        name=row['path']
        if not re.fullmatch(r'config\.json|model\.bin|tokenizer\.json|vocabulary\.(json|txt)|preprocessor_config\.json|LICENSE',name):
            raise PermissionError('Unexpected ASR model path')
        if prepared:
            source=Path(prepared)/name
            if sha(source)!=row['sha256']:raise PermissionError('Prepared ASR model checksum mismatch')
        else:source=provision_download(row,cache)
        shutil.copy2(source,target/name)
    write(target/'model-manifest.json',dict(item,format='seedance.faster-whisper-model@1'))


def verify_python_lock():
    """Refuse an accidentally different resolver result in a formal build lane."""
    record=ROOT/'requirements/python-win-x64-py314-r39-candidate.json'
    document=json.loads(record.read_text('utf8'))
    if document.get('complete') is not True or sys.version.split()[0]!=document.get('python'):
        raise RuntimeError('Build Python does not match the complete reviewed dependency lock')
    canonical=lambda name:re.sub(r'[-_.]+','-',name).lower()
    expected={canonical(row['name']):row['version'] for row in document['packages']}
    actual={canonical(distribution.metadata['Name']):distribution.version
            for distribution in importlib.metadata.distributions()}
    if actual!=expected:
        raise RuntimeError('Installed build dependencies differ from the reviewed full transitive lock')
    return {'path':record.relative_to(ROOT).as_posix(),'sha256':sha(record),'packages':len(expected)}


def verify_compiler_lock(executable):
    record=ROOT/'tools/desktop-toolchain.lock.json'
    document=json.loads(record.read_text('utf8'))
    if document.get('format')!='seedance.desktop-toolchain-lock@1':
        raise RuntimeError('Unknown desktop toolchain lock')
    parent=Path(executable).resolve().parent
    for name,checksum in document['inno']['files'].items():
        if Path(name).name!=name or not (parent/name).is_file() or sha(parent/name)!=checksum:
            raise RuntimeError('Inno compiler resources differ from the reviewed toolchain lock')
    return {'version':document['inno']['version'],'lock_sha256':sha(record),'sha256':sha(executable)}


def build(options):
    if os.name!='nt' or sys.maxsize<2**32:raise RuntimeError('Build with Windows x64 Python')
    python_lock=verify_python_lock()
    output=options.output.resolve();cache=options.cache.resolve()
    if output.exists():raise FileExistsError('Build output already exists; previous builds are preserved')
    if not options.git or not options.iscc.is_file():raise RuntimeError('Git and Inno ISCC are required')
    compiler_lock=verify_compiler_lock(options.iscc)
    output.mkdir(parents=True);cache.mkdir(parents=True,exist_ok=True)
    snapshot=output/'source-snapshot';snapshot.mkdir()
    names=source_inputs(options.git);identity={name:sha(ROOT/name) for name in names}
    for name in names:
        target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
    safety=scan_tree(snapshot)
    write(output/'source-safety.json',safety)
    if safety['findings']:raise PermissionError('Source snapshot security scan failed')
    version=versions(); lock=json.loads((snapshot/'tools/windows-runtime.lock.json').read_text('utf8'))
    archives={name:fetch(lock[name],cache) for name in ('node','browser','ffmpeg')}
    if not isinstance(lock.get('webview2'),dict):raise ValueError('Fixed WebView2 lock is required for an independently installable desktop release')
    webview_lock=lock['webview2']
    if options.webview_fixed_cab:
        fixed_archive=options.webview_fixed_cab.resolve()
        if sha(fixed_archive)!=webview_lock['sha256']:raise PermissionError('Fixed WebView2 CAB checksum mismatch')
    else:fixed_archive=fetch(webview_lock,cache)
    signed_path=str(fixed_archive).replace("'","''")
    subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        "$s=Get-AuthenticodeSignature -LiteralPath '"+signed_path+"'; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation'){exit 7}"],stderr=subprocess.STDOUT)
    staging=Path(tempfile.mkdtemp(prefix='desktop-',dir=cache))
    resources=staging/'resources';resources.mkdir()
    # Capture actual current application resources, never a prior release ZIP.
    for folder in ('static','assets','tools','core','desktop'):
        shutil.copytree(snapshot/folder,resources/folder)
    extract(archives['node'],staging/'node')
    nodebase=staging/'node'/lock['node']['archive_root']
    (resources/'tools/node').mkdir(exist_ok=True)
    shutil.copy2(nodebase/'node.exe',resources/'tools/node/node.exe')
    shutil.copy2(nodebase/'LICENSE',resources/'tools/node/NODE_UPSTREAM_LICENSE.txt')
    write(resources/'tools/node/runtime-manifest.json',{'format':'seedance.node-managed@1',
        'provenance':lock['node'],'files':{p.relative_to(resources/'tools/node').as_posix():sha(p)
            for p in sorted((resources/'tools/node').rglob('*')) if p.is_file()}})
    extract(archives['ffmpeg'],staging/'ffmpeg')
    ffroot=next((staging/'ffmpeg').iterdir())
    shutil.copytree(ffroot/'bin',resources/'tools/ffmpeg/bin')
    for item in ffroot.iterdir():
        if item.is_file():shutil.copy2(item,resources/'tools/ffmpeg'/item.name)
    write(resources/'tools/ffmpeg/runtime-manifest.json',{'format':'seedance.ffmpeg-managed@1',
        'provenance':lock['ffmpeg'],'files':{p.relative_to(resources/'tools/ffmpeg').as_posix():sha(p)
            for p in sorted((resources/'tools/ffmpeg').rglob('*')) if p.is_file()}})
    # Reconstruct the provider from the captured source and lock. Never borrow
    # a developer's mutable node_modules tree or another release directory.
    sdk=staging/'provider';shutil.copytree(snapshot/'tools/hypit_provider',sdk)
    environment={key:value for key,value in os.environ.items()
                 if not any(secret in key.upper() for secret in ('KEY','TOKEN','SECRET','PASSWORD'))}
    environment.pop('NODE_OPTIONS',None);environment.pop('NODE_PATH',None)
    environment['PATH']=str(nodebase)+os.pathsep+environment.get('PATH','')
    environment['npm_config_cache']=str(cache/'npm-cache')
    npm=nodebase/'node_modules/npm/bin/npm-cli.js'
    command([nodebase/'node.exe',npm,'ci','--ignore-scripts','--no-audit','--no-fund',
             '--registry=https://registry.npmjs.org/','--prefix',sdk],output/'npm-ci.log',timeout=600,env=environment)
    command([nodebase/'node.exe',npm,'run','build:offline','--prefix',sdk],output/'npm-provider-build.log',timeout=180,env=environment)
    original_dist=snapshot/'tools/hypit_provider/dist'
    rebuilt={p.relative_to(sdk/'dist').as_posix():sha(p) for p in (sdk/'dist').rglob('*') if p.is_file()}
    expected_dist={p.relative_to(original_dist).as_posix():sha(p) for p in original_dist.rglob('*') if p.is_file()}
    if rebuilt!=expected_dist:raise RuntimeError('Clean provider build differs from captured compiled source')
    installed=json.loads((sdk/'node_modules/.package-lock.json').read_text('utf8'))
    declared=json.loads((snapshot/'tools/hypit_provider/package-lock.json').read_text('utf8'))
    for name,row in installed.get('packages',{}).items():
        if name.startswith('node_modules/@seedance-studio/'):continue
        if name not in declared['packages'] or row.get('version')!=declared['packages'][name].get('version'):
            raise ValueError('Installed npm tree differs from repository lock: '+name)
    for relative,checksum in identity.items():
        if relative.startswith('tools/hypit_provider/') and sha(ROOT/relative)!=checksum:
            raise RuntimeError('Provider changed during snapshot')
    browser=staging/'browser';extract(archives['browser'],browser/lock['browser']['cache_root'])
    sdk_item=build_payload(sdk,resources/'tools/sdk-payload.zip')
    if getattr(options,'download_components',False):
        component_catalog=build_catalog(lock,browser,archives['browser'],host_version=version['APP_VERSION'])
        write(resources/'tools/components-profile.json',component_catalog)
        browser_item={'file':'browser-payload.zip','sha256':lock['browser']['sha256'],'deferred':True}
    else:
        browser_item=build_payload(native_path(browser),resources/'tools/browser-payload.zip')
    write(resources/'tools/hypit_bundle.json',{'format':'seedance.hypit-bundle@1','sdk':sdk_item,'browser':browser_item,
        'sdk_lazy':bool(getattr(options,'download_components',False))})
    fixed_staging=staging/'webview2';fixed_staging.mkdir()
    command(['expand.exe',str(fixed_archive),'-F:*',str(fixed_staging)],output/'webview2-expand.log',timeout=180)
    binaries=list(fixed_staging.rglob('msedgewebview2.exe'))
    if len(binaries)!=1:raise RuntimeError('Expected one Microsoft Fixed WebView2 executable')
    fixed=resources/'desktop/webview2'
    shutil.copytree(native_path(binaries[0].parent),native_path(fixed))
    write(fixed/'runtime-manifest.json',{'format':'seedance.webview2-fixed@1','version':webview_lock['version'],
        'archive':webview_lock,'publisher':'Microsoft Corporation','windows_10_appcontainer_acl_required':True,
        'files':{p.relative_to(fixed).as_posix():sha(p) for p in sorted(fixed.rglob('*')) if p.is_file()}})
    provision_ocr(lock,cache,staging,resources,output,prepared=options.tesseract_directory)
    if not getattr(options,'download_components',False):
        provision_asr(lock,cache,resources,prepared=options.asr_model_directory)
    licenses=resources/'desktop/licenses';licenses.mkdir()
    package_metadata=[]
    for distribution in importlib.metadata.distributions():
        name=distribution.metadata.get('Name','unknown')
        package_metadata.append({'name':name,'version':distribution.version,
            'license':distribution.metadata.get('License-Expression') or distribution.metadata.get('License','unknown')})
        for item in distribution.files or []:
            if '.dist-info/' not in str(item).replace('\\','/'):continue
            if not Path(str(item)).name.upper().startswith(('LICENSE','COPYING','NOTICE')):continue
            original=Path(distribution.locate_file(item))
            if original.is_file():
                target=licenses/''.join(c for c in name if c.isalnum() or c in '-_.')/Path(str(item)).name
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
    for filename in ('LICENSE.txt','LICENSE'):
        python_license=Path(sys.base_prefix)/filename
        if python_license.is_file():shutil.copy2(python_license,licenses/'PYTHON_LICENSE.txt');break
    write(licenses/'package-metadata.json',package_metadata)
    # Add all resources explicitly. Python app/core are frozen by the module graph.
    spec=output/'SeedanceStudio.spec'
    spec.write_text('''from PyInstaller.utils.hooks import collect_submodules, collect_all
import json
from pathlib import Path
datas=[]; binaries=[]; hiddenimports=['app','clr','pythonnet','webview.platforms.winforms','webview.platforms.edgechromium']
hiddenimports+=collect_submodules('core')
hiddenimports+=collect_submodules('desktop')
for package in ('yt_dlp','curl_cffi','cv2','scenedetect','alibabacloud_oss_v2','docx','pypdf','openpyxl','webview','certifi',
                'faster_whisper','ctranslate2','onnxruntime','av','tokenizers','huggingface_hub','tuf','securesystemslib','cryptography'):
    d,b,h=collect_all(package);datas+=d;binaries+=b;hiddenimports+=h
datas=[item for item in datas if not item[0].replace('\\\\','/').endswith('/yt_dlp/extractor/shahid.py')]
hiddenimports=[name for name in hiddenimports if name!='yt_dlp.extractor.shahid']
datas += [(RESOURCES+'/'+name,name) for name in ('static','assets','tools','core','desktop')]
a=Analysis([ENTRY],pathex=[SNAPSHOT],binaries=binaries,datas=datas,hiddenimports=hiddenimports,
           excludes=['pytest','tkinter','matplotlib','IPython','notebook','PyQt5','PyQt6','PySide2','PySide6','yt_dlp.extractor.shahid'],noarchive=False)
def generated_license_cache(entry):
    name=entry[0].replace('\\\\','/')
    return ('/licenses/__pycache__/' in name and name.endswith('.pyc')
            and any(part.endswith('.dist-info') for part in name.split('/')))
removed=[entry[0] for entry in a.datas if generated_license_cache(entry)]
a.datas=[entry for entry in a.datas if not generated_license_cache(entry)]
Path(CACHE_REPORT).write_text(json.dumps({'excluded_generated_license_bytecode':removed},indent=2),encoding='utf8')
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='SeedanceStudio',debug=False,bootloader_ignore_signals=False,
        strip=False,upx=False,console=False,disable_windowed_traceback=False,uac_admin=False,
        icon=SNAPSHOT+'/assets/branding/seedance-studio.ico')
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SeedanceStudio')
'''.replace('RESOURCES',repr(str(resources))).replace('ENTRY',repr(str(snapshot/'desktop/launcher.py'))).replace('SNAPSHOT',repr(str(snapshot))).replace('CACHE_REPORT',repr(str(output/'generated-metadata-cache.json'))),encoding='utf8')
    command([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',output/'dist','--workpath',staging/'pyi-work',spec],output/'pyinstaller.log')
    payload=output/'dist/SeedanceStudio'
    if not (payload/'SeedanceStudio.exe').is_file():raise RuntimeError('Frozen application missing')
    payload_safety=audit_payload(payload);write(output/'payload-safety.json',payload_safety)
    if payload_safety['findings']:raise PermissionError('Frozen payload security scan failed')
    source_changed=[name for name,value in identity.items() if not (ROOT/name).is_file() or sha(ROOT/name)!=value]
    if source_changed:
        write(output/'SOURCE_CHANGED_BLOCKED.json',{'status':'BLOCKED','files':source_changed})
        raise RuntimeError('Source changed during freeze; refusing installer publication. Capture a new snapshot.')
    installer_output=output/'installer'
    payload_files=payload_inventory(payload)
    command([options.iscc,'/DPayloadDir='+str(payload),'/DOutputDir='+str(installer_output),
             '/DVersion='+version['APP_VERSION'],'/DBuild='+version['APP_BUILD'],snapshot/'desktop/installer.iss'],output/'inno.log')
    verify_payload_unchanged(payload,payload_files,output/'PAYLOAD_CHANGED_BLOCKED.json')
    installers=list(installer_output.glob('SeedanceStudio_Setup_*.exe'))
    if len(installers)!=1:raise RuntimeError('Expected exactly one installer')
    gitsha=subprocess.check_output([options.git,'-c','safe.directory='+str(ROOT).replace('\\','/'),'-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    source_changed=[name for name,value in identity.items() if sha(ROOT/name)!=value]
    if source_changed:
        write(output/'SOURCE_CHANGED_BLOCKED.json',{'status':'BLOCKED','files':source_changed})
        raise RuntimeError('Source changed during installer build; this installer is not a deliverable.')
    manifest={'format':'seedance.desktop-build@1','version':version,'git_sha':gitsha,'source_hashes':identity,
              'working_tree_snapshot':True,'source_changed_after_snapshot':source_changed,'runtime_lock':lock,
              'python':sys.version,'python_lock':python_lock,
              'inno_compiler':compiler_lock,
              'installer':{'path':str(installers[0]),'sha256':sha(installers[0]),'bytes':installers[0].stat().st_size},
              'payload_files':payload_files,'payload_unchanged_during_packaging':True,
              'clean_windows_acceptance':'NOT TESTED','stable_release':False,'paid_calls':0}
    manifest['component_distribution']='background-pinned' if getattr(options,'download_components',False) else 'fully-bundled'
    if getattr(options,'download_components',False):manifest['deferred_components']=component_catalog
    write(output/'BUILD_MANIFEST.json',manifest)
    write(output/'SBOM.cdx.json',export_sbom(manifest,payload,
        json.loads((snapshot/python_lock['path']).read_text('utf8'))))
    (output/'SHA256SUMS.txt').write_text(sha(installers[0])+'  '+installers[0].name+'\n',encoding='utf8')
    # A fresh coworker machine runs acceptance through stock PowerShell; it
    # must not need developer Python to verify the self-contained desktop app.
    shutil.copy2(output/'BUILD_MANIFEST.json',installer_output/'BUILD_MANIFEST.json')
    shutil.copy2(output/'SBOM.cdx.json',installer_output/'SBOM.cdx.json')
    shutil.copy2(output/'SHA256SUMS.txt',installer_output/'SHA256SUMS.txt')
    shutil.copy2(snapshot/'tools/acceptance/NewPC_Verify.ps1',installer_output/'NewPC_Verify.ps1')
    for name,flag in (('01_INSTALL_AND_VERIFY.cmd',''),('02_RESUME_AFTER_REBOOT.cmd',' -Resume')):
        (installer_output/name).write_bytes(('@echo off\r\nchcp 65001 >nul\r\n'
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0NewPC_Verify.ps1"'+flag+' %*\r\n'
            'set "QA_EXIT=%ERRORLEVEL%"\r\npause\r\nexit /b %QA_EXIT%\r\n').encode('utf8'))
    command([sys.executable,'-m','pip','freeze'],output/'python-build-lock.txt',timeout=60)
    print(json.dumps(manifest['installer']),flush=True)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--iscc',type=Path,required=True)
    parser.add_argument('--webview-bootstrapper',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--webview-fixed-cab',type=Path)
    parser.add_argument('--tesseract-directory',type=Path)
    parser.add_argument('--asr-model-directory',type=Path)
    parser.add_argument('--download-components',action='store_true',
                        help='Keep core runtimes bundled; download pinned ASR weights and render browser in background')
    parser.add_argument('--git',default=shutil.which('git'))
    build(parser.parse_args())
