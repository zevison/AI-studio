"""Windows shared-runtime discovery for Seedance Studio (no model API calls).

Existing venvs are reused only after interpreter/version and dependency checks.
Windows uses an NTFS junction because it works without Developer Mode.
A reused legacy venv remains owned by its original installation directory.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.cache')) / 'SeedanceStudio'
SHARED = STORE / 'runtime' / f'py{sys.version_info.major}{sys.version_info.minor}'
ASSETS = STORE / 'assets'


def python_exe(venv: Path) -> Path:
    return venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def runtime_version(venv: Path) -> str | None:
    exe = python_exe(venv)
    if not exe.is_file():
        return None
    try:
        completed = subprocess.run([str(exe), '-c', 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
                                   capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=18)
        return completed.stdout.strip() if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def supported(venv: Path) -> bool:
    return runtime_version(venv) == f'{sys.version_info.major}.{sys.version_info.minor}'


def sibling_roots() -> list[Path]:
    """Only immediately adjacent past Seedance Studio releases; newest first."""
    try:
        roots = [p for p in ROOT.parent.iterdir() if p.is_dir() and p.resolve() != ROOT.resolve()
                 and p.name.lower().startswith(('seedancestudio_', 'seedance_studio_'))]
        return sorted(roots, key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []


def previous_venv() -> Path | None:
    for sibling in sibling_roots():
        candidate = sibling / '.venv'
        if supported(candidate):
            return candidate
    return None


def make_link(target: Path, alias: Path) -> bool:
    if alias.exists() or alias.is_symlink():
        return False
    if os.name == 'nt':
        p = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        return p.returncode == 0 and python_exe(alias).is_file()
    try:
        alias.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


def copy_previous_asset(relative: str, dest: Path, *, min_bytes: int = 4096) -> bool:
    """Restore tools already downloaded in older sibling installs or the shared cache."""
    if dest.exists() and dest.stat().st_size >= min_bytes:
        return True
    candidates = [ASSETS / relative] + [p / relative for p in sibling_roots()]
    for source in candidates:
        try:
            if source.is_file() and source.stat().st_size >= min_bytes:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
                print(f'[复用] {relative} 从已安装资源复制，无需再次下载。', flush=True)
                return True
        except OSError:
            pass
    return False


def cache_asset(relative: str, source: Path, *, min_bytes: int = 4096) -> None:
    try:
        if source.is_file() and source.stat().st_size >= min_bytes:
            target = ASSETS / relative
            if target.resolve() != source.resolve() and (not target.exists() or target.stat().st_size != source.stat().st_size):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    except OSError:
        pass
