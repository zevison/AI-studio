"""Return missing requirement specs as JSON using the target Python runtime."""
from __future__ import annotations
import importlib.metadata
import json
import sys
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement


def missing(requirements_path: Path) -> list[str]:
    result=[]
    for raw in requirements_path.read_text(encoding='utf-8').splitlines():
        raw=raw.split(' #',1)[0].strip()
        if not raw or raw.startswith('#'):
            continue
        r=Requirement(raw)
        if r.marker is not None and not r.marker.evaluate():
            continue
        try:
            installed=importlib.metadata.version(r.name)
        except importlib.metadata.PackageNotFoundError:
            result.append(raw)
            continue
        if r.specifier and installed not in r.specifier:
            result.append(raw)
    return result

if __name__=='__main__':
    print(json.dumps(missing(Path(sys.argv[1])),ensure_ascii=False))
