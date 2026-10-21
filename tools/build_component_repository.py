"""Prepare immutable plugin artifacts and offline packs locally. Never publish."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.component_manager import embedded_catalog as catalog,plain
from core.component_download import digest
from core.component_packages import export_pack


def build(resources,cache,output):
    resources,cache,output=plain(resources),plain(cache),plain(output)
    if output.exists():raise FileExistsError('Repository output already exists; never overwrite a release')
    items=catalog(resources)
    if not items:raise ValueError('No embedded optional component catalog')
    # Validate all inputs before creating a potentially incomplete release.
    for item in items.values():
        for row in item['files']:
            path=plain(cache/row['sha256'])
            if not path.is_file() or path.stat().st_size!=row['bytes'] or digest(path)!=row['sha256']:
                raise PermissionError('Missing verified artifact '+row['sha256'])
    output.mkdir(parents=True)
    artifacts=output/'artifacts';artifacts.mkdir()
    packs=output/'offline';packs.mkdir()
    for key,item in items.items():
        for row in item['files']:
            target=artifacts/row['sha256']
            if not target.exists():shutil.copy2(cache/row['sha256'],target)
        export_pack(packs/(key+'.zip'),key,item,cache)
    shutil.copy2(resources/'tools/components-profile.json',output/'catalog.json')
    shutil.copy2(ROOT/'tools/Publish_ComponentRepository.ps1',output/'Publish_ComponentRepository.ps1')
    rows=[{'path':p.relative_to(output).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)}
          for p in sorted(output.rglob('*')) if p.is_file()]
    report={'format':'seedance.component-repository@1','status':'LOCAL_ONLY_UNSIGNED_NOT_PUBLISHED',
            'trust':'Client accepts embedded pins only; catalog.json is inventory, not a trusted update feed.',
            'redistribution_clearance':'PENDING','files':rows}
    (output/'inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('resources','cache','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build(args.resources,args.cache,args.output),indent=2))
