"""Background installation of build-pinned optional data/browser components.

The installed application's local catalog is the trust boundary. This service
does not consume remote version catalogs or upgrade Python/npm at application start.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from urllib.parse import urlsplit
from .component_download import DownloadCancelled, digest, fetch,ordered_sources
from .runtime_layout import resource_root, user_home, native_path
from .component_locks import file_lease,ComponentBusy

_instances = {}
_instances_lock = threading.Lock()
_verified = {}
_verification_lock = threading.Lock()


def _assert_studio_closed():
    module=sys.modules.get('core.hypit_studio')
    studio=getattr(module,'service',None)
    process=getattr(studio,'studio_process',None)
    if process is not None and process.poll() is None:
        raise ComponentBusy('请先关闭 Hypit 官方 Studio，再切换视频合成组件')


def plain(path):
    path = native_path(path)
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
        raise PermissionError('组件目录包含链接或目录联接')
    return path


def embedded_catalog(root):
    profile = native_path(root) / 'tools/components-profile.json'
    if not profile.is_file(): return {}
    document = json.loads(profile.read_text('utf8'))
    return validate_items(document)


def validate_items(document):
    if not isinstance(document,dict):raise ValueError('组件清单格式无效')
    if document.get('format') not in {'seedance.components@1','seedance.components@2'}: raise ValueError('组件清单格式无效')
    items = document.get('components', {})
    if not isinstance(items, dict) or set(items) - {'asr', 'browser'}: raise ValueError('组件清单无效')
    for key, item in items.items():
        import re
        if not isinstance(item,dict):raise ValueError('组件清单条目无效')
        if (not isinstance(item.get('title'),str) or not 1<=len(item['title'])<=160
            or not isinstance(item.get('version'),str) or not 1<=len(item['version'])<=160):
            raise ValueError('组件名称或版本无效')
        if document['format']=='seedance.components@2':
            from .component_compatibility import validate
            validate(key,item,document.get('host_version'))
        if not item.get('files') or not item.get('installed_files'): raise ValueError('组件清单不完整')
        if (not isinstance(item['files'],list) or len(item['files'])>8
            or not isinstance(item['installed_files'],dict) or len(item['installed_files'])>5000):
            raise ValueError('组件清单文件数量无效')
        for row in item['files']:
            if (not isinstance(row,dict) or type(row.get('bytes')) is not int or not 0<row['bytes']<=800*1024**2
                or not isinstance(row.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',row['sha256'])
                or not isinstance(row.get('url'),str) or len(row['url'])>4096):
                raise ValueError('组件下载身份无效')
            from .component_download import validate_url
            validate_url(row['url'],row['url'])
            if 'path' in row and (not isinstance(row['path'],str) or not re.fullmatch(r'[A-Za-z0-9_./-]+',row['path'])
                or '..' in row['path'].split('/') or row['path'].startswith('/')):
                raise ValueError('组件下载文件路径无效')
        if document['format']=='seedance.components@2':
            if key=='asr' and {r.get('path') for r in item['files']}!={'config.json','model.bin','tokenizer.json','vocabulary.txt'}:
                raise ValueError('语音组件缺少完整的已支持模型文件')
            if key=='asr' and item['installed_bytes']!=sum(row['bytes'] for row in item['files']):
                raise ValueError('语音组件安装大小与实际文件不一致')
            if key=='browser' and (not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',item.get('version',''))
                or item.get('cache_root')!='chrome-headless-shell/win64-'+item['version']
                or item['cache_root']+'/chrome-headless-shell-win64/chrome-headless-shell.exe' not in item['installed_files']):
                raise ValueError('浏览器版本与安装目录不匹配')
        for name, checksum in item['installed_files'].items():
            from pathlib import PurePosixPath
            import re
            if (not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or '..' in PurePosixPath(name).parts
                    or name.startswith('/') or not re.fullmatch('[a-f0-9]{64}', checksum)):
                raise ValueError('组件清单路径或哈希无效')
    return items


def catalog(root,home=None):
    items=embedded_catalog(root)
    if not items:return items
    from .component_updates import safe_overlay
    return safe_overlay(root,home if home is not None else user_home(),items)


def identity(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def location(home, key, item):
    return plain(Path(home) / 'components' / key / identity(item)[:16])


def validate_installed(folder, item):
    """Hash all expected files once per observed metadata fingerprint, not per UI poll."""
    folder = plain(folder)
    fingerprints = []
    try:
        for name in item['installed_files']:
            path = plain(folder / name)
            st = path.stat()
            if not path.is_file(): return False
            fingerprints.append((name, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
    except FileNotFoundError:
        return False
    cache_key = (str(folder), identity(item))
    with _verification_lock:
        if _verified.get(cache_key) == fingerprints: return True
        for name, checksum in item['installed_files'].items():
            if digest(folder / name) != checksum: return False
        _verified[cache_key] = fingerprints
    return True


def resolve(key, *, root=None, home=None):
    root = Path(root) if root is not None else resource_root()
    items = catalog(root,home=home)
    if key not in items: return None
    destination = location(home if home is not None else user_home(), key, items[key])
    if not validate_installed(destination, items[key]):
        raise FileNotFoundError('本地组件尚未就绪或校验失败，请打开“设置 → 插件与组件”下载或重试')
    return destination


@contextmanager
def process_lease(path):
    """OS lock releases on crash; the file itself is never used as a readiness flag."""
    path = plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc: raise ComponentBusy('另一进程正在下载组件') from exc
        else:
            import fcntl
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc: raise ComponentBusy('另一进程正在下载组件') from exc
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def using(key,*,root=None,home=None):
    root=resource_root() if root is None else root
    home=user_home() if home is None else home
    if key not in catalog(root,home=home):
        yield None
        return
    with file_lease(plain(Path(home)/'components/leases'/(key+'.lock')),shared=key=='browser'):
        yield resolve(key,root=root,home=home)


class ComponentManager:
    def __init__(self, root, home, *, downloader=fetch,health_checker=None):
        self.root, self.home = Path(root), plain(home)
        self.items = catalog(self.root,home=self.home)
        self.base = plain(self.home / 'components')
        self.downloader = downloader
        from .component_health import check_in_subprocess
        self.health_checker=health_checker or (lambda root,key,folder,item:check_in_subprocess(root,self.home,key,folder,item))
        self.lock = threading.RLock()
        self.thread = None
        self.cancel_event = threading.Event()
        self.stop_event = None
        self.active = None
        self.receiving=False
        self.update_thread=None
        self.update_result={}
        self.states = {}
        self.last_save = 0
        self.last_logged={}
        self.preferences=self._read_preferences()
        state_path = self.base / 'state.json'
        if state_path.is_file():
            try:
                old = json.loads(state_path.read_text('utf8'))
                for key, item in self.items.items():
                    value = old.get(key, {})
                    if value.get('identity') == identity(item):
                        if value.get('state')=='receiving':
                            value.update(state='failed',interrupted=False,error='离线包上传未完成，请重新导入；不会改为联网下载')
                        elif value.get('state') in {'queued', 'downloading', 'verifying', 'installing', 'pausing','health_check'}:
                            value.update(state='paused', interrupted=value.get('state')!='pausing', error='上次下载被中断，可点击继续；已有断点已保留')
                        self.states[key] = value
            except (ValueError, OSError):
                # Do not destroy unreadable history or silently overwrite it.
                raise ValueError('组件状态文件不可读，原文件已保留')
        # A recorded interrupted activation is the only reason to inspect large
        # component files at startup. Ordinary UI startup remains metadata-only.
        for key,item in self.items.items():
            destination=location(self.home,key,item)
            if destination.with_name(destination.name+'.activation.json').is_file():
                try:
                    with process_lease(self.base/'download.lock'),file_lease(self.base/'leases'/(key+'.lock')):
                        from .component_activation import recover
                        recover(destination,item)
                except ComponentBusy:pass  # Another process owns the transaction.

    def _read_preferences(self):
        path=plain(self.base/'preferences.json')
        value={'automatic_on_demand':False,'resume_interrupted':False,'source_order':'domestic_first','approved':{}}
        if path.is_file():
            document=json.loads(path.read_text('utf8'))
            if not isinstance(document,dict) or not isinstance(document.get('approved'),dict):
                raise ValueError('组件授权设置损坏，原文件已保留')
            value.update(document)
        if (any(type(value.get(name)) is not bool for name in ('automatic_on_demand','resume_interrupted'))
            or value.get('source_order') not in {'domestic_first','official_first'}):
            raise ValueError('组件授权设置类型无效，原文件已保留')
        return value

    def _save_preferences(self):
        self.base.mkdir(parents=True,exist_ok=True)
        temporary=plain(self.base/('preferences.'+uuid.uuid4().hex+'.tmp'))
        with temporary.open('x',encoding='utf8') as stream:
            json.dump(self.preferences,stream,ensure_ascii=False)
            stream.flush();os.fsync(stream.fileno())
        temporary.replace(plain(self.base/'preferences.json'))

    def configure(self,request):
        if not isinstance(request,dict) or set(request)-{'automatic_on_demand','resume_interrupted','source_order'}:
            raise ValueError('组件下载设置无效')
        for name in ('automatic_on_demand','resume_interrupted'):
            if name in request and type(request[name]) is not bool:raise ValueError('组件下载开关必须为布尔值')
        if request.get('source_order','domestic_first') not in {'domestic_first','official_first'}:
            raise ValueError('未知组件来源优先级')
        with self.lock,process_lease(self.base/'preferences.lock'):
            if self.active and 'source_order' in request:raise ComponentBusy('请等待当前组件任务结束后更换来源优先级')
            self.preferences=self._read_preferences()
            previous=dict(self.preferences)
            self.preferences.update(request)
            try:self._save_preferences()
            except Exception:self.preferences=previous;raise
            return self.status()

    def _save(self):
        self.base.mkdir(parents=True, exist_ok=True)
        temporary = plain(self.base / ('state.' + uuid.uuid4().hex + '.tmp'))
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(self.states, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        temporary.replace(plain(self.base / 'state.json'))
        for key,value in self.states.items():
            signature=(value.get('state'),value.get('error'))
            if self.last_logged.get(key)!=signature:
                try:
                    log=plain(self.base/'components.log')
                    if log.is_file() and log.stat().st_size>256*1024:
                        previous=plain(self.base/'components.previous.log')
                        if previous.exists():previous.unlink()
                        log.rename(previous)
                    with log.open('a',encoding='utf8') as stream:
                        stream.write(json.dumps({'time':time.time(),'component':key,'state':signature[0],
                            'error':signature[1],'source':value.get('source','')},ensure_ascii=False)+'\n')
                    self.last_logged[key]=signature
                except OSError as exc:
                    # State durability above is mandatory; optional text logging
                    # must not turn a committed installation into false failure.
                    logging.getLogger(__name__).warning('Component transition log unavailable: %s',type(exc).__name__)

    def logs(self):
        path=plain(self.base/'components.log')
        if not path.is_file():return {'text':'尚无组件操作日志'}
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-65536));raw=stream.read(65536)
        return {'text':raw.decode('utf8','replace')}

    def plugin_update_status(self):
        from . import component_updates as updates
        try:
            enabled=updates.policy(self.root) is not None
            value={'state':'ready' if enabled else 'BLOCKED',
                   'reason':'' if enabled else '尚未配置正式插件签名信任根及更新源；现有锁定组件仍可安装和使用',
                   'automatic_install':False,'catalog_warning':updates.warning(self.root,self.home)}
            return dict(value,job=dict(self.update_result))
        except Exception:
            return {'state':'BLOCKED','reason':'插件更新信任配置校验失败；不会联网或安装','automatic_install':False,'job':{}}

    def check_updates(self,key):
        from . import component_updates as updates
        if not isinstance(key,str) or key not in self.items:raise ValueError('未知组件')
        if updates.policy(self.root) is None:raise PermissionError('尚未配置正式插件签名信任根及更新源；不会联网')
        with self.lock:
            if self.active:raise ComponentBusy('请等待当前组件任务结束后检查更新')
            if self.update_thread and self.update_thread.is_alive():raise ComponentBusy('正在检查插件更新')
            self.update_result={'state':'running','id':key}
            def run():
                try:
                    with process_lease(self.base/'updates/check.lock'):
                        result=updates.check(self.root,self.home,key)
                    state='up_to_date' if identity(result['item'])==identity(catalog(self.root,home=self.home)[key]) else 'update_available'
                    with self.lock:self.update_result=dict(state=state,id=key,**{k:v for k,v in result.items() if k!='item'})
                except Exception as exc:
                    # Never echo remote response bodies or credential-shaped URLs.
                    with self.lock:self.update_result={'state':'failed','id':key,'error':type(exc).__name__+'：插件签名、网络或兼容检查失败，现有版本未改变'}
            self.update_thread=threading.Thread(target=run,daemon=True,name='plugin-update-check');self.update_thread.start()
            return self.status()

    def install_update(self,key,*,confirmed=False):
        from . import component_updates as updates
        if confirmed is not True:raise PermissionError('需要确认插件版本、用途及下载大小')
        if not isinstance(key,str) or key not in self.items:raise ValueError('未知组件')
        with self.lock:
            if self.active or (self.update_thread and self.update_thread.is_alive()):raise ComponentBusy('另一个组件任务正在执行')
            proof_id=updates.selection(self.home,'candidate',key)
            item=updates.admit_candidate(self.root,self.home,key,proof_id)
            self.items[key]=item
            return self.begin(key,confirmed=True,force=True)

    def rollback_update(self,key,*,confirmed=False):
        from . import component_updates as updates
        if confirmed is not True:raise PermissionError('需要确认回退到保留的已验证插件版本')
        if not isinstance(key,str) or key not in self.items:raise ValueError('未知组件')
        with self.lock,process_lease(self.base/'download.lock'),process_lease(self.base/'updates/check.lock'):
            if self.active or (self.update_thread and self.update_thread.is_alive()):raise ComponentBusy('组件任务正在执行')
            with file_lease(self.base/'leases'/(key+'.lock')):
                if key=='browser':_assert_studio_closed()
                item=updates.rollback_item(self.root,self.home,key)
                folder=location(self.home,key,item)
                if not validate_installed(folder,item):raise PermissionError('保留的旧组件不完整，未切换当前版本')
                health=self.health_checker(self.root,key,folder,item)
                if not health.get('available'):raise RuntimeError('旧组件未通过真实健康检查，当前版本未改变')
                updates.rollback(self.root,self.home,key);self.items[key]=item
                self.states[key]=dict(identity=identity(item),state='ready',health=health,error='',note='已按确认回退；新版目录保留，用户数据未改变')
                self._save()
            return self.status()

    def clear_cache(self,*,confirmed=False):
        if confirmed is not True:raise PermissionError('需要确认清理缓存；修复或离线导出可能需要重新下载')
        with self.lock,process_lease(self.base/'download.lock'):
            if self.active:raise ComponentBusy('组件任务执行中，不能清理缓存')
            cache=plain(self.base/'cache')
            count=freed=0;failures=0;stages=0
            hashes={row['sha256'] for item in self.items.values() for row in item['files']}
            if cache.is_dir():
                import re
                for path in cache.iterdir():
                    match=re.fullmatch(r'([a-f0-9]{64})(?:\.part(?:\.json)?|\.part\.json\.[a-f0-9]{32}\.tmp)?(?:\.rejected-[a-f0-9]{32})?',path.name)
                    if not match or match[1] not in hashes:continue
                    plain(path)
                    if not path.is_file():continue
                    try:
                        size=path.stat().st_size;path.unlink();freed+=size;count+=1
                    except OSError:failures+=1
            import re
            for key in self.items:
                parent=plain(self.base/key)
                if not parent.is_dir():continue
                for path in parent.iterdir():
                    if not re.fullmatch(r'\.install-[a-f0-9]{32}',path.name):continue
                    candidate=plain(path)
                    if candidate.resolve().parent!=parent.resolve():raise PermissionError('临时组件目录超出清理范围')
                    if not candidate.is_dir():continue
                    try:
                        # Verify the entire deletion target before recursive removal;
                        # never follow a link planted in a retained staging tree.
                        stage_bytes=0
                        for node in candidate.rglob('*'):
                            plain(node)
                            if node.is_file():stage_bytes+=node.stat().st_size
                        shutil.rmtree(candidate);stages+=1;freed+=stage_bytes
                    except OSError:failures+=1
            return {'removed_files':count,'freed_bytes':freed,'retained_busy_files':failures,
                    'removed_interrupted_stages':stages,
                    'note':'仅清理本程序生成的下载缓存；已安装组件、配置、素材、历史任务及安装包均未删除'}

    def status(self,*,verify=False):
        from .component_updates import can_rollback
        with self.lock:
            rows = []
            for key, item in self.items.items():
                state = dict(self.states.get(key, {}))
                folder=location(self.home,key,item)
                current_item=catalog(self.root,home=self.home).get(key,item)
                current_folder=location(self.home,key,current_item)
                if self.active != key:
                    previously_checked=(str(folder),identity(item)) in _verified
                    installed=validate_installed(folder,item) if verify or previously_checked else False
                    if installed and not state.get('health_error'): state.update(state='ready', error='')
                    elif installed:state['state']='needs_repair'
                    elif state.get('state')=='ready':
                        state.update(state='needs_repair' if verify or previously_checked else 'installed',
                                     error='组件文件缺失或发生变化，请修复' if verify or previously_checked else '')
                    if identity(current_item)!=identity(item) and state.get('state') in {'ready','installed'}:
                        state.update(state='needs_repair',error='当前签名版本选择与此组件不同，未将候选版本标记为可用')
                rows.append(dict(id=key, title=item['title'], version=item['version'],
                                 display_version=item.get('display_version',item['version']),
                                 current_display_version=current_item.get('display_version',current_item['version']),
                                 can_rollback=can_rollback(self.root,self.home,key),
                                 current_version=current_item['version'] if current_folder.is_dir() else None,
                                 installed_bytes=item.get('installed_bytes',sum(row['bytes'] for row in item['files'])),
                                 purpose=item.get('purpose','本地运行组件'),dependencies=item.get('dependencies',[]),
                                 reviewed_sources=list(dict.fromkeys(urlsplit(url).hostname
                                     for row in item['files'] for url in ordered_sources(row,self.preferences['source_order']))),
                                 manual_files=[dict(name=row.get('path') or urlsplit(row['url']).path.rsplit('/',1)[-1],
                                     bytes=row['bytes'],sha256=row['sha256'],
                                     urls=ordered_sources(row,self.preferences['source_order']),
                                     cached=(self.base/'cache'/row['sha256']).is_file()) for row in item['files']],
                                 install_path=str(location(self.home,key,item)).removeprefix('\\\\?\\'),
                                 approved=self.preferences['approved'].get(key)==identity(item),
                                 total=sum(row['bytes'] for row in item['files']),
                                 **{k: v for k, v in state.items() if k not in {'identity', 'total','offline_pack'}}))
            return {'components': rows, 'active': self.active,
                    'plugin_updates':self.plugin_update_status(),
                    'preferences':{key:value for key,value in self.preferences.items() if key!='approved'},
                    'update_policy': '内置版本由安装包锁定；独立插件更新须通过 TUF 签名、发布序列及主程序兼容检查。镜像不能自行选择新版本，未配置正式信任根时更新关闭。旧版文件保留。'}

    def begin(self, key, *, confirmed=False,offline_pack=None,force=False):
        with self.lock:
            if not isinstance(key,str) or key not in self.items: raise ValueError('未知组件')
            if self.receiving:raise ValueError('正在接收离线组件包，请稍候')
            if self.thread and self.thread.is_alive():
                if self.active == key: return self.status()
                raise ValueError('正在下载其他组件，请完成后继续')
            item = self.items[key]
            old_state=self.states.get(key,{})
            if offline_pack is None and old_state.get('offline_required') and not force:
                name=old_state.get('offline_pack','')
                import re
                if not re.fullmatch(r'[a-f0-9]{32}\.zip',name):
                    raise ValueError('请重新导入完整离线包；不会自动转为联网下载')
                offline_pack=plain(self.base/'imports'/name)
                if not offline_pack.is_file():raise FileNotFoundError('离线包缓存缺失，请重新导入；不会转为联网下载')
            if (not force and offline_pack is None and not self.states.get(key,{}).get('health_error')
                and validate_installed(location(self.home,key,item),item)):return self.status()
            if self.preferences['approved'].get(key)!=identity(item):
                if confirmed is not True:raise PermissionError('首次使用此功能，需要确认安装运行组件及下载大小')
                with process_lease(self.base/'preferences.lock'):
                    self.preferences=self._read_preferences()
                    self.preferences['approved'][key]=identity(item)
                    self._save_preferences()
            self.cancel_event.clear()
            self.active = key
            self.states[key] = dict(identity=identity(item), state='queued', downloaded=0, source='', error='')
            if offline_pack is not None:
                package=plain(offline_pack)
                self.states[key]['offline_required']=True
                if package.parent==plain(self.base/'imports'):
                    import re
                    if re.fullmatch(r'[a-f0-9]{32}\.zip',package.name):self.states[key]['offline_pack']=package.name
            try:self._save()
            except Exception:
                self.active=None
                self.states[key].update(state='failed',error='组件任务无法持久保存，请检查目录写入权限；尚未下载')
                raise
            self.thread = threading.Thread(target=self._perform, args=(key,offline_pack), daemon=True, name='component-download')
            self.thread.start()
            return self.status()

    def receive_import(self,key,stream,length,*,confirmed=False,artifact=False):
        folder=plain(self.base/'imports')
        package=plain(folder/(uuid.uuid4().hex+'.zip'))
        with self.lock:
            if not isinstance(key,str) or key not in self.items:raise ValueError('未知组件')
            if confirmed is not True:raise PermissionError('需要确认导入离线组件')
            if self.active or self.receiving:raise ValueError('另一个组件任务尚未结束')
            maximum=sum(row['bytes'] for row in self.items[key]['files'])+1024**2
            if type(length) is not int or not 0<length<=maximum:raise ValueError('离线包大小超出锁定组件范围')
            if artifact and length not in {row['bytes'] for row in self.items[key]['files']}:
                raise ValueError('原始下载文件大小不符，请使用本版本提供的链接')
            self.base.mkdir(parents=True,exist_ok=True)
            required=max(3*length,2*length+self.items[key].get('installed_bytes',length))+256*1024**2
            if shutil.disk_usage(self.home).free<required:raise OSError('磁盘空间不足，不能导入离线包')
            self.receiving=True;self.active=key
            self.cancel_event.clear()
            self.states[key]=dict(identity=identity(self.items[key]),state='receiving',downloaded=0,source='离线导入',error='')
        started=time.monotonic();done=0;success=False
        try:
            with self.lock:self._save()
            folder.mkdir(parents=True,exist_ok=True)
            with package.open('xb') as output:
                while done<length:
                    if self._cancelled():raise DownloadCancelled()
                    if time.monotonic()-started>300:raise TimeoutError('离线包上传超时')
                    block=stream.read(min(128*1024,length-done))
                    if not block:raise ValueError('离线包上传中断')
                    output.write(block);done+=len(block)
                    with self.lock:self.states[key]['downloaded']=done
                output.flush();os.fsync(output.fileno())
            with self.lock:
                self.receiving=False;self.active=None
                if artifact:
                    from .component_packages import cache_artifact,export_pack
                    item=self.items[key];cache_artifact(package,item,self.base/'cache')
                    # Export uses full SHA verification of every file. There is
                    # no network fallback for a partially imported manual set.
                    cached=sum((self.base/'cache'/row['sha256']).is_file() for row in item['files'])
                    if cached==len(item['files']):
                        export_pack(package,key,item,self.base/'cache')
                        result=self.begin(key,confirmed=True,offline_pack=package)
                    else:
                        self.states[key].update(state='paused',offline_required=True,
                            error=f'已校验并导入 {cached}/{len(item["files"])} 个文件，请继续导入其余原始文件；不会自动联网')
                        self._save();result=self.status()
                else:result=self.begin(key,confirmed=True,offline_pack=package)
            success=True
            return result
        finally:
            if not success:
                if package.exists():package.unlink()
                with self.lock:
                    self.receiving=False;self.active=None
                    self.states[key].update(state='failed',error='离线包未完整接收，原组件和用户文件未更改')
                    self._save()

    def cancel(self, key):
        with self.lock:
            if key != self.active: raise ValueError('组件当前没有下载任务')
            self.cancel_event.set()
            self.states[key]['state']='pausing'
            self._save()
            return self.status()

    def _cancelled(self):
        return self.cancel_event.is_set() or bool(self.stop_event and self.stop_event.is_set())

    def _perform(self, key,offline_pack=None):
        item = self.items[key]
        staging = None
        owns_lease = False
        phase='download'
        try:
            with process_lease(self.base / 'download.lock'):
                owns_lease = True
                total_bytes=sum(row['bytes'] for row in item['files'])
                required=max(3*total_bytes,total_bytes+item.get('installed_bytes',total_bytes))+256*1024**2
                if shutil.disk_usage(self.home).free < required:
                    raise OSError('磁盘空间不足，组件下载需要缓存及安装空间')
                completed = 0
                paths = []
                samples=deque()
                def progress(**value):
                    with self.lock:
                        now=time.monotonic();count=completed+value['downloaded']
                        while len(samples)>1 and now-samples[0][0]>3:samples.popleft()
                        samples.append((now,count))
                        elapsed=now-samples[0][0]
                        speed=max(0,(count-samples[0][1])/elapsed) if elapsed>.25 else 0
                        total=sum(row['bytes'] for row in item['files'])
                        self.states[key].update(state='pausing' if self.cancel_event.is_set() else 'verifying' if value['source'] == 'SHA-256' else 'downloading',
                            downloaded=count,source=value['source'],bytes_per_second=round(speed),
                            eta_seconds=round((total-count)/speed) if speed else None)
                        if time.monotonic() - self.last_save > 1:
                            self._save(); self.last_save = time.monotonic()
                if offline_pack is not None:
                    from .component_packages import import_pack
                    paths=import_pack(offline_pack,key,item,self.base/'cache',cancelled=self._cancelled,progress=progress)
                    completed=sum(row['bytes'] for row in item['files'])
                else:
                    for row in item['files']:
                        paths.append(self.downloader(row, self.base / 'cache', progress=progress, cancelled=self._cancelled,
                            source_order=self.preferences['source_order']))
                        completed += row['bytes']
                if self._cancelled(): raise DownloadCancelled()
                destination = location(self.home, key, item)
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging = plain(destination.with_name('.install-' + uuid.uuid4().hex))
                staging.mkdir()
                phase='install'
                with self.lock: self.states[key].update(state='installing', downloaded=completed); self._save()
                if key == 'asr':
                    for row, path in zip(item['files'], paths): shutil.copy2(path, staging / row['path'])
                else:
                    sys.path.insert(0, str(self.root / 'tools'))
                    if 'installed_bytes' in item:
                        import zipfile
                        with zipfile.ZipFile(paths[0]) as archive:
                            if sum(row.file_size for row in archive.infolist())!=item['installed_bytes']:
                                raise PermissionError('浏览器实际展开大小与已签名或内置清单不一致')
                    from install_hypit_bundle import extract_verified
                    extract_verified(paths[0], staging / item['cache_root'], item['files'][0]['sha256'])
                if not validate_installed(staging, item): raise PermissionError('组件安装后校验失败')
                phase='health_check'
                with self.lock:self.states[key].update(state='health_check');self._save()
                health=self.health_checker(self.root,key,staging,item)
                if not health.get('available'):raise RuntimeError('组件没有通过实际运行检测')
                if self._cancelled(): raise DownloadCancelled()
                # No replacing working older versions; damaged same-version bytes are retained.
                with file_lease(plain(self.base/'leases'/(key+'.lock'))):
                    if key=='browser':
                        _assert_studio_closed()
                    from .component_activation import activate
                    activation=activate(staging,destination,item);staging=None
                    from .component_updates import activate as activate_selection
                    selected=activate_selection(self.root,self.home,key,item)
                with self.lock:
                    self.states[key].update(state='ready',health=health,activation=activation,health_error=False,error='')
                    if selected:self.update_result={'state':'up_to_date','id':key,'version':item.get('display_version',item['version'])}
                    self.states[key].pop('offline_pack',None);self.states[key].pop('offline_required',None)
                    self._save()
        except DownloadCancelled:
            with self.lock:
                self.states[key].update(state='paused',interrupted=not self.cancel_event.is_set(),error='下载已暂停，断点已保留')
                self._save()
        except Exception as exc:
            with self.lock:
                self.states[key].update(state='failed' if phase=='download' else 'install_failed',
                    health_error=phase=='health_check',error=str(exc)[:400])
                if owns_lease:self._save()
        finally:
            try:
                if staging is not None and staging.exists():
                    # Only the private staging directory created above; never user data.
                    if plain(staging).parent == location(self.home, key, item).parent and staging.name.startswith('.install-'):
                        try:shutil.rmtree(staging)
                        except OSError:
                            # Retain only our private staging area. In-use native
                            # files must never turn cleanup into an unhandled
                            # worker exception or erase the original failure.
                            with self.lock:
                                self.states[key]['cleanup_warning']='组件临时文件仍被占用，已保留现场；可关闭相关功能后重试，用户数据未删除'
                                try:self._save()
                                except OSError:pass  # The original terminal state was already persisted.
            finally:
                try:
                    if offline_pack is not None and self.states[key].get('state')!='paused':
                        original=plain(offline_pack)
                        import re
                        if (original.parent==plain(self.base/'imports') and re.fullmatch(r'[a-f0-9]{32}\.zip',original.name)
                            and original.is_file()):original.unlink()
                except OSError:
                    with self.lock:self.states[key]['cleanup_warning']='临时离线包暂时被占用，后续可清理；原组件未删除'
                finally:
                    with self.lock: self.active = None

    def need(self,key):
        if not isinstance(key,str) or key not in self.items:raise ValueError('未知组件')
        with self.lock:
            if (self.preferences['automatic_on_demand']
                and self.preferences['approved'].get(key)==identity(self.items[key])
                and not self.active and self.states.get(key,{}).get('state') not in {'paused','failed','install_failed','needs_repair'}):
                self.begin(key)
            return self.status()

    def uninstall(self,key,*,confirmed=False):
        if confirmed is not True:raise PermissionError('需要确认移除组件；素材、配置和历史记录不会删除')
        with self.lock,process_lease(self.base/'download.lock'):
            if not isinstance(key,str) or key not in self.items:raise ValueError('未知组件')
            if self.active:raise ValueError('组件任务执行中，不能卸载')
            with file_lease(plain(self.base/'leases'/(key+'.lock'))):
                if key=='browser':
                    _assert_studio_closed()
                target=location(self.home,key,self.items[key])
                if target.exists():target.rename(target.with_name(target.name+'.removed-'+uuid.uuid4().hex))
                with process_lease(self.base/'preferences.lock'):
                    self.preferences=self._read_preferences()
                    self.preferences['approved'].pop(key,None);self._save_preferences()
                self.states[key]=dict(identity=identity(self.items[key]),state='not_installed',downloaded=0,
                    error='',note='组件已移除；原目录和缓存保留，可重新确认安装。用户素材与历史记录未变更')
                self._save()
            return self.status()

    def repair(self,key,*,confirmed=False):
        if confirmed is not True:raise PermissionError('需要确认重新校验并修复运行组件')
        return self.begin(key,confirmed=True,force=True)

    def start_initial(self, stop_event):
        self.stop_event = stop_event
        def initial():
            for key in self.items:
                if stop_event.is_set(): return
                # Application startup never authorizes a new plugin download.
                state=self.states.get(key,{})
                if (not self.preferences['resume_interrupted'] or not state.get('interrupted')
                    or self.preferences['approved'].get(key)!=identity(self.items[key])):continue
                self.begin(key)
                thread = self.thread
                if thread:
                    while thread.is_alive() and not stop_event.wait(.5): pass
        threading.Thread(target=initial, daemon=True, name='initial-components').start()


def service():
    key = (str(resource_root()), str(user_home()))
    with _instances_lock:
        if key not in _instances: _instances[key] = ComponentManager(*key)
        return _instances[key]
