"""Inspect pinned domestic fallback mappings; network/full downloads are explicit opt-in."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.component_download import fetch,sources_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true',help='Actually download all pinned files, never execute them')
    parser.add_argument('--cache',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    options=parser.parse_args()
    if options.download and options.cache is None:parser.error('--download requires --cache')
    lock=json.loads((ROOT/'tools/windows-runtime.lock.json').read_text('utf8'))
    rows=[('node',lock['node']),('browser',lock['browser'])]+[('asr/'+row['path'],row) for row in lock['asr']['files']]
    results=[]
    for name,row in rows:
        result={'component':name,'sha256':row['sha256'],'bytes':row['bytes'],'sources':sources_for(row),'status':'NOT_TESTED'}
        if options.download:
            seen=set();started=time.monotonic()
            def progress(**value):
                if value['source']!='SHA-256':seen.add(value['source'])
            try:
                artifact=fetch(row,options.cache,progress=progress)
                result.update(status='PASS',verified_file=str(artifact),network_hosts_observed=sorted(seen),
                    evidence='full downloaded SHA-256 verified' if seen else 'existing cache SHA-256 verified; no live connectivity claim')
            except Exception as exc:result.update(status='FAIL',error=type(exc).__name__+': '+str(exc))
            result['seconds']=round(time.monotonic()-started,3)
        results.append(result)
        options.report.parent.mkdir(parents=True,exist_ok=True)
        options.report.write_text(json.dumps({'format':'seedance.component-sources@1','automatic_upgrade':False,'results':results},ensure_ascii=False,indent=2),encoding='utf8')
    return int(any(row['status']=='FAIL' for row in results))

if __name__=='__main__':raise SystemExit(main())
