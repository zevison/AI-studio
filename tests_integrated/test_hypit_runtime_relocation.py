import hashlib
import json
from pathlib import Path

import pytest

from core import hypit_runtime_relocation as relocation


def project(tmp_path):
    folder = tmp_path / '旧工程 中文'
    folder.mkdir()
    (folder / 'main.svml').write_text('<svml><media:Video src="./assets/material-1.mp4"/></svml>', encoding='utf8')
    (folder / 'assets').mkdir()
    (folder / 'assets/material-1.mp4').write_bytes(b'unchanged-media')
    profile = {'format': 'hypit.runtime-local@1', 'dataRoot': '.hypit/execution', 'endpoints': {
        'media.local': {'use': '@hypit/media-local', 'pool': 'LOCAL-FREE', 'config': {
            'ffmpegPath': r'C:\old\ffmpeg.exe', 'ffprobePath': r'C:\old\ffprobe.exe'}},
        'html.local': {'use': '@hypit/provider-html-local', 'pool': 'LOCAL-HTML', 'config': {
            'workers': 1, 'maxWorkers': 1, 'browserCapacity': 1, 'defaultConcurrency': 1,
            'browserVersion': '152.0.7928.2', 'browserCacheDirectory': r'C:\old\browser-cache',
            'ffmpegPath': r'C:\old\ffmpeg.exe', 'ffprobePath': r'C:\old\ffprobe.exe'}}}}
    (folder / 'hypit.runtime.json').write_text(json.dumps(profile), encoding='utf8')
    return folder, profile


def relocate(folder, **options):
    return relocation.relocate_runtime(folder, ffmpeg=r'C:\new\ffmpeg.exe', ffprobe=r'C:\new\ffprobe.exe',
                                       browser_version='155.0.8059.84', browser_cache=r'C:\new\browser-cache', **options)


def test_rebind_preserves_source_assets_and_exact_backup(tmp_path):
    folder, _ = project(tmp_path)
    original = (folder / 'hypit.runtime.json').read_bytes()
    source = (folder / 'main.svml').read_bytes()
    result = relocate(folder)
    assert result['changed'] and result['requires_preflight']
    assert Path(result['backup']).read_bytes() == original
    assert result['before_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['after_sha256'] == hashlib.sha256((folder / 'hypit.runtime.json').read_bytes()).hexdigest()
    assert (folder / 'main.svml').read_bytes() == source
    assert (folder / 'assets/material-1.mp4').read_bytes() == b'unchanged-media'
    options = json.loads((folder / 'hypit.runtime.json').read_text('utf8'))['endpoints']['html.local']['config']
    assert options['browserVersion'] == '155.0.8059.84'
    assert options['browserCacheDirectory'] == r'C:\new\browser-cache'
    assert options['ffmpegPath'] == r'C:\new\ffmpeg.exe'
    assert not relocate(folder)['changed']


def test_custom_browser_path_removes_mutually_exclusive_selection(tmp_path):
    folder, _ = project(tmp_path)
    relocate(folder, chrome_path=r'C:\new\chrome.exe')
    options = json.loads((folder / 'hypit.runtime.json').read_text('utf8'))['endpoints']['html.local']['config']
    assert options['chromePath'] == r'C:\new\chrome.exe'
    assert 'browserVersion' not in options
    assert 'browserCacheDirectory' not in options


@pytest.mark.parametrize('mutation', ['remote', 'unknown-executable', 'pool', 'bad-data-root'])
def test_unrecognized_runtime_is_not_rewritten(tmp_path, mutation):
    folder, profile = project(tmp_path)
    if mutation == 'remote':
        profile['endpoints']['generation.remote'] = {'use': '@hypit/seedance', 'pool': 'PAID', 'config': {}}
    elif mutation == 'unknown-executable':
        profile['endpoints']['html.local']['config']['nodePath'] = r'C:\evil.exe'
    elif mutation == 'pool':
        profile['endpoints']['html.local']['pool'] = 'FOREIGN'
    else:
        profile['dataRoot'] = r'C:\outside'
    file = folder / 'hypit.runtime.json'
    file.write_text(json.dumps(profile), encoding='utf8')
    original = file.read_bytes()
    with pytest.raises(PermissionError):
        relocate(folder)
    assert file.read_bytes() == original
    assert not (folder / '.runtime-relocation').exists()


@pytest.mark.parametrize('reference', [r'C:\old\assets\cup.png', '/old/assets/cup.png', 'file:///old/cup.png', 'https://example.test/cup.png', '&#67;:\\old\\cup.png'])
def test_absolute_business_reference_blocks_without_modifying_any_source(tmp_path, reference):
    folder, _ = project(tmp_path)
    source = folder / 'main.svml'
    source.write_text('<svml><media:Video src="' + reference + '"/></svml>', encoding='utf8')
    original = source.read_bytes()
    runtime = (folder / 'hypit.runtime.json').read_bytes()
    with pytest.raises(PermissionError, match='重新绑定素材'):
        relocate(folder)
    assert source.read_bytes() == original
    assert (folder / 'hypit.runtime.json').read_bytes() == runtime


def test_active_or_unknown_project_cannot_change_runtime(tmp_path):
    folder, _ = project(tmp_path)
    original = (folder / 'hypit.runtime.json').read_bytes()
    with pytest.raises(PermissionError, match='结果未知'):
        relocate(folder, busy=True)
    assert (folder / 'hypit.runtime.json').read_bytes() == original


def test_existing_backup_is_never_overwritten(tmp_path):
    folder, _ = project(tmp_path)
    original = (folder / 'hypit.runtime.json').read_bytes()
    backups = folder / '.runtime-relocation'
    backups.mkdir()
    backup = backups / (hashlib.sha256(original).hexdigest() + '.runtime.json')
    backup.write_bytes(b'do not overwrite')
    with pytest.raises(PermissionError, match='备份已变化'):
        relocate(folder)
    assert backup.read_bytes() == b'do not overwrite'
    assert (folder / 'hypit.runtime.json').read_bytes() == original


def test_atomic_publication_failure_preserves_original_and_backup(tmp_path, monkeypatch):
    folder, _ = project(tmp_path)
    original = (folder / 'hypit.runtime.json').read_bytes()
    actual = relocation._atomic
    def fail(path, raw):
        if path.name == 'hypit.runtime.json':
            raise OSError('injected publication failure')
        return actual(path, raw)
    monkeypatch.setattr(relocation, '_atomic', fail)
    with pytest.raises(OSError):
        relocate(folder)
    assert (folder / 'hypit.runtime.json').read_bytes() == original
    assert next((folder / '.runtime-relocation').glob('*.runtime.json')).read_bytes() == original


def test_profile_link_is_rejected(tmp_path, monkeypatch):
    folder, _ = project(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda self: self.name == 'hypit.runtime.json' or original(self))
    with pytest.raises(PermissionError, match='链接'):
        relocate(folder)
