"""Independent plugin catalogs: TUF online admission, signed proofs offline.

Installed bytes remain usable offline after metadata expiry. New admissions must
pass a fresh TUF refresh. Production is disabled without an embedded trust root.
No request may supply a root key, repository URL or installation command.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import logging
import os
import uuid
import time
import copy
from datetime import datetime,timezone
from urllib.parse import urlsplit

from .update_security import TrustPolicy,UpdateBlocked,UpdateRejected,public_fetcher

_verified={}
_lock=threading.RLock()
_warnings={}


def _plain(path):
    from .component_manager import plain
    return plain(path)


def _write(path,value):
    from .update_service import atomic_json
    atomic_json(path,value)


def _store(path,raw):
    path=_plain(path)
    if path.exists():
        if _read(path)!=raw:raise UpdateRejected('插件签名存档发生冲突')
        return
    temporary=_plain(path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp'))
    try:
        with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def _read(path,maximum=2*1024**2):
    path=_plain(path)
    if not path.is_file() or path.stat().st_size>maximum:raise UpdateRejected('插件签名记录缺失或超限')
    return path.read_bytes()


def policy(root):
    folder=_plain(Path(root)/'assets');file=folder/'plugin-update-trust.json'
    if not file.is_file():return None
    doc=json.loads(_read(file,65536))
    if doc.get('format')!='seedance.plugin-update-trust@1' or doc.get('product_id')!='SeedanceStudio':
        raise UpdateRejected('插件更新信任配置不属于本产品')
    if doc.get('enabled') is not True:return None
    if doc.get('root_file')!='plugin-update-root.json':raise UpdateRejected('插件信任根文件名无效')
    bootstrap=_read(folder/'plugin-update-root.json',512000)
    if hashlib.sha256(bootstrap).hexdigest()!=doc.get('root_sha256'):raise UpdateRejected('插件信任根哈希不匹配')
    from .configuration_service import validate_base_url
    urls=[]
    for name in ('metadata_url','targets_url'):
        value=validate_base_url(doc[name]).rstrip('/')+'/'
        if urlsplit(value).scheme!='https':raise UpdateRejected('插件更新只接受 HTTPS')
        urls.append(value)
    return TrustPolicy(bootstrap,*urls,(),max_target_bytes=256*1024)


def abi(root):
    """Bind a reviewed plugin release to this host's actual bundled contract."""
    from .component_manager import embedded_catalog
    embedded_catalog(root)  # Validate the immutable host catalog first.
    values=[]
    for name in ('tools/windows-runtime.lock.json','tools/hypit_bundle.json'):
        file=Path(root)/name
        values.append((name,hashlib.sha256(_read(file)).hexdigest() if file.is_file() else None))
    return hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()


def directory(home):return _plain(Path(home)/'components/updates')


def _target(key):
    if key not in {'asr','browser'}:raise UpdateRejected('未知插件')
    return 'SeedanceStudio/plugins/'+key+'.json'


def _validate_release(root,key,document):
    from .component_manager import validate_items
    host=json.loads(_read(Path(root)/'tools/components-profile.json'))
    if (document.get('format')!='seedance.plugin-release@1' or document.get('product_id')!='SeedanceStudio'
        or document.get('id')!=key or document.get('host_abi_sha256')!=abi(root)
        or type(document.get('sequence')) is not int or document['sequence']<1):
        raise UpdateRejected('插件身份、主程序 ABI 或发布序列不兼容')
    item=document.get('component')
    validate_items({'format':'seedance.components@2','host_version':host.get('host_version'),'components':{key:item}})
    return item


def verify_proof(root,key,proof,*,trust=None):
    from tuf.api.metadata import Metadata,Root,Targets
    trust=trust or policy(root)
    if trust is None:raise UpdateBlocked('尚未配置正式插件签名信任根及更新源；现有插件仍可使用')
    if proof.get('format')!='seedance.plugin-proof@1':raise UpdateRejected('插件签名证明格式无效')
    roots=proof.get('roots')
    if not isinstance(roots,list) or not 1<=len(roots)<=64:raise UpdateRejected('插件根签名链超限')
    current=Metadata.from_bytes(trust.bootstrap)
    if not isinstance(current.signed,Root):raise UpdateRejected('插件信任根类型无效')
    current.verify_delegate('root',current)
    if Metadata.from_dict(copy.deepcopy(roots[0])).signed_bytes!=current.signed_bytes:
        raise UpdateRejected('插件根签名链起点不可信')
    for value in roots[1:]:
        next_root=Metadata.from_dict(copy.deepcopy(value))
        if not isinstance(next_root.signed,Root) or next_root.signed.version!=current.signed.version+1:
            raise UpdateRejected('插件根签名链不连续')
        current.verify_delegate('root',next_root);next_root.verify_delegate('root',next_root)
        current=next_root
    targets_raw=proof['targets'].encode('utf8')
    targets=Metadata.from_bytes(targets_raw)
    if not isinstance(targets.signed,Targets):raise UpdateRejected('插件目标签名类型无效')
    current.verify_delegate('targets',targets)
    snapshot_raw=proof['snapshot'].encode('utf8');snapshot=Metadata.from_bytes(snapshot_raw)
    timestamp=Metadata.from_bytes(proof['timestamp'].encode('utf8'))
    current.verify_delegate('snapshot',snapshot);current.verify_delegate('timestamp',timestamp)
    target_meta=snapshot.signed.meta['targets.json']
    if target_meta.version!=targets.signed.version or timestamp.signed.snapshot_meta.version!=snapshot.signed.version:
        raise UpdateRejected('插件签名元数据版本关联不一致')
    target_meta.verify_length_and_hashes(targets_raw)
    timestamp.signed.snapshot_meta.verify_length_and_hashes(snapshot_raw)
    target=targets.signed.targets.get(_target(key))
    if target is None or target.length>trust.max_target_bytes:raise UpdateRejected('签名目标中缺少受限大小的插件清单')
    raw=proof.get('release','').encode('utf8')
    target.verify_length_and_hashes(raw)
    document=json.loads(raw)
    return _validate_release(root,key,document),document


def load_proof(root,home,key,proof_id,*,trust=None,fresh=False):
    if not isinstance(proof_id,str) or not re.fullmatch('[a-f0-9]{64}',proof_id):raise UpdateRejected('插件证明身份无效')
    raw=_read(directory(home)/'proofs'/(proof_id+'.json'))
    if hashlib.sha256(raw).hexdigest()!=proof_id:raise UpdateRejected('插件签名证明内容发生变化')
    trust=trust or policy(root)
    if trust is None:raise UpdateBlocked('尚未配置正式插件签名信任根及更新源')
    proof=json.loads(raw)
    if fresh:
        from tuf.api.metadata import Metadata
        now=datetime.now(timezone.utc)
        values=[Metadata.from_dict(copy.deepcopy(proof['roots'][-1])),*[Metadata.from_bytes(proof[name].encode('utf8')) for name in ('timestamp','snapshot','targets')]]
        if any(value.signed.is_expired(now) for value in values):raise UpdateRejected('插件更新签名元数据已过期，请重新检查更新')
    stamp=(str(Path(root)),str(home),key,proof_id,abi(root),hashlib.sha256(trust.bootstrap).hexdigest())
    with _lock:
        if stamp not in _verified:
            _verified[stamp]=verify_proof(root,key,proof,trust=trust)
            if len(_verified)>128:_verified.pop(next(iter(_verified)))
        return _verified[stamp]


def selection(home,name,key):
    path=directory(home)/(name+'.json')
    if not path.is_file():return None
    doc=json.loads(_read(path,65536))
    if not isinstance(doc,dict):raise UpdateRejected('插件版本选择记录损坏')
    return doc.get(key)


def overlay(root,home,items):
    result=dict(items)
    if policy(root) is None:return result
    for key in result:
        selected=selection(home,'active',key)
        if selected:result[key]=load_proof(root,home,key,selected)[0]
    return result


def safe_overlay(root,home,items):
    stamp=(str(root),str(home))
    try:
        result=overlay(root,home,items);_warnings.pop(stamp,None);return result
    except Exception as exc:
        # Optional signed catalogs must never prevent the base application from
        # starting. Reject the entire overlay and retain immutable bundled pins.
        _warnings[stamp]='插件签名或兼容记录未通过验证，已拒绝更新并回到内置版本；请检查更新日志'
        if len(_warnings)>32:_warnings.pop(next(iter(_warnings)))
        logging.getLogger(__name__).warning('Plugin catalog rejected: %s',type(exc).__name__)
        return dict(items)


def warning(root,home):return _warnings.get((str(root),str(home)))


def can_rollback(root,home,key):
    try:
        active=selection(home,'active',key)
        return policy(root) is not None and bool(active) and active!=selection(home,'previous',key)
    except Exception:return False


def admit_candidate(root,home,key,proof_id):
    from .component_manager import identity
    item,document=load_proof(root,home,key,proof_id,fresh=True)
    highest=selection(home,'highest',key)
    if not highest or highest.get('sequence')!=document['sequence'] or highest.get('identity')!=identity(item):
        raise UpdateRejected('插件候选不再是最近获准的发布序列，请重新检查更新')
    return item


def _set(home,name,key,value):
    path=directory(home)/(name+'.json')
    document=json.loads(_read(path,65536)) if path.is_file() else {}
    document[key]=value;_write(path,document)


def check(root,home,key,*,trust=None,fetcher=None):
    """Fresh TUF admission. Caller holds the cross-process update lease."""
    trust=trust or policy(root)
    if trust is None:raise UpdateBlocked('尚未配置正式插件签名信任根及更新源；不会联网或安装')
    from tuf.ngclient import Updater
    from tuf.ngclient.config import UpdaterConfig
    from tuf.ngclient.fetcher import FetcherInterface
    from tuf.api.metadata import Metadata
    base=directory(home);metadata=_plain(base/'metadata');metadata.mkdir(parents=True,exist_ok=True)
    _plain(base/'targets').mkdir(exist_ok=True)
    for file in metadata.glob('*.json'):_plain(file)
    captured={};delegate=fetcher or public_fetcher(trust);deadline=time.monotonic()+120
    class Recorder(FetcherInterface):
        def _fetch(self,url):
            if time.monotonic()>deadline:raise TimeoutError('插件更新检查已超过时限')
            parts=[];size=0
            for block in delegate.fetch(url):
                if time.monotonic()>deadline:raise TimeoutError('插件更新检查已超过时限')
                size+=len(block)
                if size>2*1024**2:raise UpdateRejected('插件元数据响应超限')
                parts.append(block);yield block
            if re.fullmatch(r'\d+\.root\.json',url.rsplit('/',1)[-1]):captured[url.rsplit('/',1)[-1]]=b''.join(parts)
    updater=Updater(str(metadata),trust.metadata_url,str(base/'targets'),trust.targets_url,
                    fetcher=Recorder(),bootstrap=trust.bootstrap,config=UpdaterConfig(max_root_rotations=32,max_delegations=0))
    updater.refresh()
    target=updater.get_targetinfo(_target(key))
    if target is None or target.length>trust.max_target_bytes:raise UpdateRejected('可信仓库暂无兼容插件清单')
    downloaded=updater.download_target(target)
    raw=_read(downloaded,trust.max_target_bytes)
    history=_plain(base/'roots');history.mkdir(exist_ok=True)
    first=Metadata.from_bytes(trust.bootstrap).signed.version
    root_file=history/(str(first)+'.root.json')
    _store(root_file,trust.bootstrap)
    for name,value in captured.items():
        file=_plain(history/name)
        if file.exists() and file.read_bytes()!=value:raise UpdateRejected('根版本签名重放冲突')
        _store(file,value)
    last=Metadata.from_bytes(_read(metadata/'root.json')).signed.version
    if not first<=last<first+64:raise UpdateRejected('需要先升级主程序的插件信任根')
    proof={'format':'seedance.plugin-proof@1','release':raw.decode('utf8'),
           'roots':[json.loads(_read(history/(str(n)+'.root.json'))) for n in range(first,last+1)],
           **{name:_read(metadata/(name+'.json')).decode('utf8') for name in ('targets','snapshot','timestamp')}}
    item,document=verify_proof(root,key,proof,trust=trust)
    from .component_manager import identity
    highest=selection(home,'highest',key)
    if highest and (document['sequence']<highest['sequence'] or
        (document['sequence']==highest['sequence'] and identity(item)!=highest['identity'])):
        raise UpdateRejected('插件发布序列回滚或重放冲突')
    encoded=json.dumps(proof,sort_keys=True,separators=(',',':')).encode()
    if len(encoded)>2*1024**2:raise UpdateRejected('插件签名证明超过支持大小，需要先升级主程序信任根')
    proof_id=hashlib.sha256(encoded).hexdigest();proofs=_plain(base/'proofs');proofs.mkdir(exist_ok=True)
    file=proofs/(proof_id+'.json')
    _store(file,encoded)
    _set(home,'highest',key,{'sequence':document['sequence'],'identity':identity(item)})
    _set(home,'candidate',key,proof_id)
    return {'proof_id':proof_id,'version':item.get('display_version',item['version']),
            'download_bytes':sum(row['bytes'] for row in item['files']), 'installed_bytes':item['installed_bytes'],
            'notes':str(document.get('notes',''))[:4000],'sequence':document['sequence'],'item':item}


def activate(root,home,key,item):
    """Called after actual native health and version-directory activation."""
    from .component_manager import identity
    if policy(root) is None:return False
    proof_id=selection(home,'candidate',key)
    if not proof_id:return False
    candidate,_=load_proof(root,home,key,proof_id)
    if identity(candidate)!=identity(item):return False
    previous=selection(home,'active',key)
    if previous and identity(load_proof(root,home,key,previous)[0])==identity(item):return True
    admit_candidate(root,home,key,proof_id)
    _set(home,'previous',key,previous)
    _set(home,'active',key,proof_id)
    return True


def proof_for(root,home,key,item):
    from .component_manager import identity
    if policy(root) is None:return None
    for name in ('active','candidate','previous'):
        value=selection(home,name,key)
        if value and identity(load_proof(root,home,key,value)[0])==identity(item):return value
    return None


def rollback_item(root,home,key):
    from .component_manager import embedded_catalog
    if not selection(home,'active',key):raise UpdateBlocked('当前组件没有独立更新可回退')
    previous=selection(home,'previous',key)
    return load_proof(root,home,key,previous)[0] if previous else embedded_catalog(root)[key]


def rollback(root,home,key):
    rollback_item(root,home,key)  # Validate before mutating either pointer.
    active=selection(home,'active',key);previous=selection(home,'previous',key)
    _set(home,'previous',key,active);_set(home,'active',key,previous)
