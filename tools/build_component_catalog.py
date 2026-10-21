"""Generate an embedded, immutable component catalog from verified build inputs."""
import hashlib
from pathlib import Path
import re


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def build_catalog(lock, browser_folder, browser_archive,*,host_version='5.2.0'):
    browser_folder, browser_archive = Path(browser_folder), Path(browser_archive)
    if sha(browser_archive) != lock['browser']['sha256']: raise PermissionError('Browser archive differs from build lock')
    rows = [dict(row) for row in lock['asr']['files']]
    if {row['path'] for row in rows} != {'config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'}:
        raise ValueError('ASR catalog requires the reviewed complete small model')
    for row in rows:
        if not re.fullmatch('[a-f0-9]{64}', row['sha256']) or type(row['bytes']) is not int or row['bytes'] <= 0:
            raise ValueError('Invalid model identity')
    files = {}
    installed_bytes=0
    for path in sorted(browser_folder.rglob('*')):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)(): raise PermissionError('Browser payload contains links')
        if path.is_file():
            files[path.relative_to(browser_folder).as_posix()] = sha(path)
            installed_bytes+=path.stat().st_size
    expected = lock['browser']['cache_root'] + '/chrome-headless-shell-win64/chrome-headless-shell.exe'
    if expected not in files: raise ValueError('Complete pinned browser executable is required')
    document={'format': 'seedance.components@2', 'host_version':host_version,'components': {
        'asr': {'title': '本地语音识别', 'version': lock['asr']['revision'], 'files': rows,
                'display_version':'small / '+lock['asr']['revision'][:12],
                'installed_bytes':sum(row['bytes'] for row in rows),
                'dependencies':['core.asr-engine'],'purpose':'在本机识别视频语音，不上传音轨，不产生模型调用费用',
                'install_type':'model-files','health_check':'load-small-cpu-int8',
                'installed_files': {row['path']: row['sha256'] for row in rows}},
        'browser': {'title': '视频合成浏览器', 'version': lock['browser']['version'],
                    'display_version':lock['browser']['version'],'installed_bytes':installed_bytes,
                    'dependencies':['core.node','core.ffmpeg','core.hypit-sdk'],
                    'purpose':'用于官方 Hypit 的本地画面合成，不会替换日常使用的浏览器',
                    'install_type':'browser-zip','health_check':'chrome-version',
                    'cache_root': lock['browser']['cache_root'],
                    'files': [dict(lock['browser'], bytes=browser_archive.stat().st_size)],
                    'installed_files': files}}}
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from core.component_download import sources_for
    for key,item in document['components'].items():
        item.update(id=key,os='windows',architecture='x64',
                    app_minimum='5.2.0',app_maximum_exclusive='6.0.0',rollback='versioned-retained')
        for row in item['files']:row['reviewed_sources']=sources_for(row)
    return document
