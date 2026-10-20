"""Scan a prepared complete packet using the current user's real vault, in memory."""
import argparse
import importlib.util
import json
from pathlib import Path
import zipfile
from release_candidate import PRIVATE,tree_hash,write


def scan_packet(packet,profile_module):
    spec=importlib.util.spec_from_file_location('release_vault',profile_module)
    vault=importlib.util.module_from_spec(spec);spec.loader.exec_module(vault)
    if not vault.SECRETS_PATH.is_file():raise PermissionError('Real vault is unavailable; do not claim a successful credential scan')
    values=[v for v in vault.read_secrets().values() if isinstance(v,str) and len(v)>=8]
    if not values:raise PermissionError('No real vault values could be read for comparison')
    patterns=[v.encode(encoding) for v in values for encoding in ('utf8','utf-16-le','utf-16-be')]
    patterns.extend(v.encode(encoding) for v in ('OSSAccess'+'KeyId=','x-oss-'+'signature=') for encoding in ('utf8','utf-16-le','utf-16-be'))
    overlap=max(map(len,patterns))-1
    def check(stream,name):
        tail=b''
        for block in iter(lambda:stream.read(1024*1024),b''):
            data=tail+block
            if any(x in data for x in patterns):raise PermissionError('Possible credential or signed-URL leak in '+name)
            tail=data[-overlap:]
    for path in sorted(packet.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(packet)
        if path.is_symlink() or any(x.casefold() in PRIVATE for x in relative.parts):raise PermissionError('Private packet entry: '+relative.as_posix())
        if path.suffix.lower()=='.zip':
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:raise ValueError('Nested runtime CRC failed')
                for item in archive.infolist():
                    if item.is_dir():continue
                    with archive.open(item) as stream:check(stream,relative.as_posix()+'/'+item.filename)
        else:
            with path.open('rb') as stream:check(stream,relative.as_posix())
    result={'passed':True,'tree_sha256':tree_hash(packet),'actual_vault_values_scanned':True,
            'utf8_and_utf16_checked':True,'nested_runtime_archives_scanned':True,'private_data_excluded':True,
            'known_signed_oss_url_patterns_checked':True,'credential_values_disclosed':False}
    write(packet/'Studio/docs/CREDENTIAL_SCAN.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--packet',type=Path,required=True)
    args=parser.parse_args()
    result=scan_packet(args.packet,Path(__file__).resolve().parents[1]/'core/profile_store.py')
    print(json.dumps(result))
