"""Find a functioning Tesseract OCR executable across Windows installation layouts.

This file never downloads, installs, mutates PATH, or makes network calls.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from . import config


def candidate_paths(*, root=None, env=None, which=None, registry_paths=()):
    """Enumerate candidates without treating the current shell PATH as authoritative."""
    env = os.environ if env is None else env
    root = Path(config.ROOT if root is None else root)
    which = shutil.which('tesseract') if which is None else which
    paths = [root / 'tools' / 'tesseract' / 'tesseract.exe',
             root / 'tools' / 'runtime' / 'bin' / 'tesseract.exe',
             root / 'tools' / 'runtime' / 'Tesseract-OCR' / 'tesseract.exe']
    if getattr(sys, 'frozen', False):
        return paths
    paths.append(which)
    for key in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)'):
        value = env.get(key)
        if value:
            paths.append(Path(value) / 'Tesseract-OCR' / 'tesseract.exe')
    local = env.get('LOCALAPPDATA')
    if local:
        paths.append(Path(local) / 'Programs' / 'Tesseract-OCR' / 'tesseract.exe')
    paths += list(registry_paths)
    dedup = []
    seen = set()
    for item in paths:
        if not item:
            continue
        p = Path(item)
        text = str(p).casefold()
        if text not in seen:
            seen.add(text)
            dedup.append(p)
    return dedup


def _registry_paths():
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    result = []
    paths = [r'SOFTWARE\Tesseract-OCR',
             r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Tesseract-OCR_is1',
             r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Tesseract-OCR_is1']
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in paths:
            try:
                with winreg.OpenKey(hive, subkey) as reg:
                    for key in ('InstallLocation', 'InstallPath', 'Path'):
                        try:
                            value, _ = winreg.QueryValueEx(reg, key)
                        except OSError:
                            continue
                        if isinstance(value, str) and value.strip():
                            folder = Path(value.strip().strip('"'))
                            result.append(folder if folder.name.lower() == 'tesseract.exe' else folder / 'tesseract.exe')
            except OSError:
                continue
    return result


def find_tesseract(*, root=None, env=None, which=None, registry_paths=None):
    reg = _registry_paths() if registry_paths is None else registry_paths
    for path in candidate_paths(root=root, env=env, which=which, registry_paths=reg):
        try:
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return None


def ocr_command(binary, image):
    """Use packaged language data explicitly; never inherit another install's data."""
    command = [str(binary), str(image), 'stdout', '--psm', '6']
    data = Path(binary).parent/'tessdata'
    if (data/'eng.traineddata').is_file():
        languages = 'eng+chi_sim' if (data/'chi_sim.traineddata').is_file() else 'eng'
        command += ['--tessdata-dir', str(data), '-l', languages]
    elif getattr(sys, 'frozen', False):
        raise FileNotFoundError('安装包缺少 OCR 语言数据，请修复安装')
    return command


def check_tesseract(*, root=None, env=None, which=None, registry_paths=None):
    binary = find_tesseract(root=root, env=env, which=which, registry_paths=registry_paths)
    if not binary:
        return {'available': False, 'path': None, 'version': None, 'reason': '未检测到安装程序'}
    try:
        args = [binary, '--version']
        opts = {'capture_output': True, 'text': True, 'timeout': 12, 'errors': 'replace'}
        if os.name == 'nt':
            opts['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        proc = subprocess.run(args, **opts)
        line = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip().splitlines()
        summary = line[0][:180] if line else ''
        if proc.returncode != 0 or 'tesseract' not in summary.lower():
            return {'available': False, 'path': binary, 'version': None, 'reason': '程序存在但运行验证失败'}
        return {'available': True, 'path': binary, 'version': summary, 'reason': None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'available': False, 'path': binary, 'version': None, 'reason': type(exc).__name__}
