"""Immutable application resources and per-user writable state are distinct."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def native_path(path) -> Path:
    """Extended Windows paths for managed data; preserve UNC and existing prefixes."""
    value=os.path.abspath(os.fspath(path))
    if os.name=='nt' and not value.startswith('\\\\?\\'):
        value=('\\\\?\\UNC\\'+value[2:]) if value.startswith('\\\\') else ('\\\\?\\'+value)
    return Path(value)


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])).resolve()


def user_home() -> Path:
    explicit = os.getenv('STUDIO_DATA_HOME', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        return (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'SeedanceStudio').resolve()
    return resource_root()


def module_command(module: str, *arguments: str) -> list[str]:
    """Frozen applications are not general Python interpreters."""
    if module != 'yt_dlp':
        raise ValueError('Unsupported packaged worker module')
    prefix = [sys.executable, '--worker-module', module] if getattr(sys, 'frozen', False) else [sys.executable, '-m', module]
    return [*prefix, *arguments]
