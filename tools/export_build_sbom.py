"""Export factual CycloneDX inventory from an already captured desktop build.

Build environment entries are labelled; this is not a license clearance report.
"""
from __future__ import annotations
import argparse,base64,json,zipfile
from pathlib import Path
from urllib.parse import quote


def export(manifest,payload,python_lock):
    components=[]
    def add(kind,name,version,ref,**extra):
        row={'type':kind,'name':name,'version':version,'bom-ref':ref,**extra}
        components.append(row)
        return row
    for package in python_lock['packages']:
        name=package['name'];version=package['version'];purl='pkg:pypi/'+name+'@'+version
        row=add('library',name,version,purl,purl=purl,
            hashes=[{'alg':'SHA-256','content':package['sha256']}],
            properties=[{'name':'seedance:scope','value':'locked Python build and runtime environment; not every package is shipped'},
                        {'name':'seedance:artifact','value':package['filename']}])
        if package.get('url'):
            row['externalReferences']=[{'type':'distribution','url':package['url']}]
    archive=payload/'_internal/tools/sdk-payload.zip'
    with zipfile.ZipFile(archive) as sdk:
        installed=json.loads(sdk.read('node_modules/.package-lock.json'))
        for location,package in sorted(installed['packages'].items()):
            name=location.rsplit('node_modules/',1)[-1];version=package.get('version','0.0.0')
            purl='pkg:npm/'+quote(name,safe='/')+'@'+version
            row=add('library',name,version,'npm:'+location,purl=purl,
                properties=[{'name':'seedance:scope','value':'actual packaged npm tree'},
                            {'name':'seedance:location','value':location}])
            if package.get('license'):
                row['licenses']=[{'license':{'name':package['license']}}]
            integrity=package.get('integrity','')
            if integrity.startswith('sha512-'):
                row['hashes']=[{'alg':'SHA-512','content':base64.b64decode(integrity[7:],validate=True).hex()}]
            if package.get('resolved'):
                row['externalReferences']=[{'type':'distribution','url':package['resolved']}]
    for name,item in manifest['runtime_lock'].items():
        if not isinstance(item,dict) or not item.get('version'):continue
        row=add('application',name,item['version'],'native:'+name,
            properties=[{'name':'seedance:scope','value':
                'pinned deferred download; not inside installer' if name in manifest.get('deferred_components',{}).get('components',{})
                else 'managed native runtime; archive provenance'}])
        if item.get('sha256'):row['hashes']=[{'alg':'SHA-256','content':item['sha256']}]
        if item.get('url'):row['externalReferences']=[{'type':'distribution','url':item['url']}]
    for name,item in manifest.get('deferred_components',{}).get('components',{}).items():
        if name=='browser':continue  # Recorded above with distribution scope.
        for file in item['files']:
            add('data',name+'/'+file['path'],item['version'],'deferred:'+name+'/'+file['path'],
                hashes=[{'alg':'SHA-256','content':file['sha256']}],
                properties=[{'name':'seedance:scope','value':'pinned deferred download; not inside installer'}],
                externalReferences=[{'type':'distribution','url':file['url']}])
    for item in manifest['payload_files']:
        if Path(item['path']).suffix.lower() not in {'.exe','.dll','.pyd'}:continue
        add('file',item['path'],'1','file:'+item['path'],
            hashes=[{'alg':'SHA-256','content':item['sha256']}],
            properties=[{'name':'seedance:bytes','value':str(item['bytes'])},
                        {'name':'seedance:license-review','value':'See bundled notices; transitive native redistribution clearance pending'}])
    version=manifest['version']['APP_VERSION']+'-'+manifest['version']['APP_BUILD']
    return {'bomFormat':'CycloneDX','specVersion':'1.6','version':1,
        'metadata':{'component':{'type':'application','name':'Seedance Studio','version':version},
            'properties':[{'name':'seedance:installer-sha256','value':manifest['installer']['sha256']},
                          {'name':'seedance:limitations','value':'Inventory only; no formal license clearance or complete native vulnerability assessment'}]},
        'components':components}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--payload',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--python-lock',required=True,type=Path)
    args=parser.parse_args()
    result=export(json.loads(args.manifest.read_text('utf8')),args.payload,json.loads(args.python_lock.read_text('utf8')))
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
