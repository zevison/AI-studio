"""Keep native inference compatible with Windows Unicode and extended paths."""
from contextlib import ExitStack
import os
from pathlib import Path
from .runtime_layout import native_path


def cpu_supported():
    if os.name!='nt':return True
    import ctypes
    return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))


def load_local_model(factory, folder):
    folder=native_path(folder)
    text=str(folder)
    if os.name=='nt' and text.startswith('\\\\?\\') and not text.startswith('\\\\?\\UNC\\'):
        text=text[4:]
    options={'device':'cpu','compute_type':'int8','local_files_only':True}
    if os.name!='nt' or (text.isascii() and len(text)<240):
        return factory(text,**options)
    # CTranslate2's native path reader cannot open every Windows Unicode/long
    # path. Its supported file-like API avoids an extra 484 MB bytes allocation.
    with ExitStack() as stack:
        files={name:stack.enter_context((folder/name).open('rb'))
               for name in ('config.json','model.bin','vocabulary.txt')}
        files['tokenizer.json']=(folder/'tokenizer.json').read_bytes()
        return factory('seedance-reviewed-local-small',files=files,**options)
