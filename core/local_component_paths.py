"""One resolver for the optional WhisperX executable across health and analysis."""
import os
from pathlib import Path
import shutil
import sys


def whisperx_path(root,*,frozen=None,executable=None,which=None):
    frozen=getattr(sys,'frozen',False) if frozen is None else frozen
    name='whisperx.exe' if os.name=='nt' else 'whisperx'
    managed=Path(root)/'tools/runtime/whisperx_py313'/('Scripts' if os.name=='nt' else 'bin')/name
    if frozen:
        # An unrelated PATH install can load unpinned Python/Torch/models and
        # override the packaged ASR. Only an explicitly bundled sidecar applies.
        return str(managed) if managed.is_file() else None
    current=Path(executable or sys.executable).parent/name
    for path in (current,managed):
        if path.is_file():return str(path)
    return (which or shutil.which)('whisperx')


def local_asr_status():
    # Lazy to keep the path facade independent during module initialization.
    from .hypit_engine import faster_whisper_status
    return faster_whisper_status()
