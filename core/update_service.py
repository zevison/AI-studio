"""Durable TUF update workflow. Production stays disabled without release trust.

The application must call assert_admission_open inside the same control-DB
transaction used to claim new work. Mutating HTTP routes must also respect it.
No installer is selected by a frontend path, URL, trust root or command line.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from .sqlite_snapshot import snapshot_database
from .sqlite_snapshot import _native
from .update_security import (PRODUCT_ID, Release, UpdateBlocked, UpdateRejected,
    load_bundled_policy, plain_path, public_fetcher, progress_fetcher, sha256, validate_release,
    verify_authenticode, verify_package)

BUSY_STATES = ('pending', 'running', 'result_pending', 'authoring', 'building', 'validating',
    'exporting', 'submitting', 'in_flight', 'calling', 'submitted', 'submission_unknown',
    'paid_result_unknown', 'unknown', 'usage_unverified', 'over_budget')


def atomic_json(path, value):
    path=plain_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd, temporary=tempfile.mkstemp(prefix='.update-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)


def control_file(home):return plain_path(Path(home)/'data/team-control.sqlite')


def _gate_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS studio_update_gate(slot INTEGER PRIMARY KEY CHECK(slot=1),transaction_id TEXT NOT NULL,state TEXT NOT NULL,updated REAL NOT NULL)')


def assert_admission_open(home, connection=None):
    """Pass the already-BEGIN-IMMEDIATE team admission connection when claiming."""
    if connection is not None:
        _gate_schema(connection)
        row=connection.execute('SELECT transaction_id,state FROM studio_update_gate WHERE slot=1').fetchone()
    else:
        file=control_file(home)
        if not file.exists():return
        conn=sqlite3.connect(file,timeout=5)
        try:
            found=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='studio_update_gate'").fetchone()
            row=conn.execute('SELECT transaction_id,state FROM studio_update_gate WHERE slot=1').fetchone() if found else None
        finally:conn.close()
    if row:raise UpdateBlocked('更新维护事务尚未结束，已暂停新任务和写入；历史任务不会自动重提')


def _table_states(connection):
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        quote=lambda name:'"'+name.replace('"','""')+'"'
        columns={row[1] for row in connection.execute('PRAGMA table_info('+quote(table)+')')}
        for column in ('status','state','phase'):
            if column in columns:
                query='SELECT 1 FROM '+quote(table)+' WHERE lower('+quote(column)+') IN ('+','.join('?' for _ in BUSY_STATES)+') LIMIT 1'
                if connection.execute(query,BUSY_STATES).fetchone():
                    raise UpdateBlocked('有执行中或结果未知的任务，禁止安装更新；请先完成恢复和账单核对')


def database_files(home):
    root=plain_path(Path(home)/'data')
    if not root.exists():return []
    files=[]
    for folder,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in {'cache','backups','.trash'}]
        for name in dirs+names:plain_path(Path(folder)/name)
        for name in names:
            path=Path(folder)/name
            if path.suffix.lower() in {'.db','.sqlite','.sqlite3'}:
                files.append(path)
                if len(files)>2000:raise UpdateBlocked('数据库数量超出更新安全检查上限')
    return files


def business_fingerprint(path):
    """Conservative rollback guard: changes to any business table stop restore."""
    conn=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    digest=hashlib.sha256()
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise UpdateRejected('数据库完整性验证失败')
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            if table in {'app_meta','studio_update_gate'}:continue
            quote='"'+table.replace('"','""')+'"'
            query='SELECT * FROM '+quote
            if table=='radar_options':
                query+=" WHERE key NOT IN ('runtime_last_start','runtime_previous_start')"
            records=[json.dumps(row,ensure_ascii=False,default=lambda value: {'blob_sha256':hashlib.sha256(value).hexdigest()},separators=(',',':'))
                     for row in conn.execute(query)]
            # New empty migration tables contain no business writes. Startup
            # timestamps above are operational metadata, not user decisions.
            if not records:continue
            digest.update(json.dumps([table,sorted(records)],ensure_ascii=False,separators=(',',':')).encode())
    finally:conn.close()
    return digest.hexdigest()


class UpdateHistory:
    def __init__(self,home):
        self.directory=plain_path(Path(home)/'updates');self.directory.mkdir(parents=True,exist_ok=True)
        self.file=self.directory/'history.sqlite'
        with self.connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,created REAL,state TEXT,target_id TEXT,message TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,identity TEXT UNIQUE,release_json TEXT NOT NULL,path TEXT,state TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS highest(channel TEXT PRIMARY KEY,sequence INTEGER NOT NULL,identity TEXT NOT NULL)')
    @contextmanager
    def connection(self):
        conn=sqlite3.connect(self.file,timeout=15);conn.row_factory=sqlite3.Row
        try:
            yield conn;conn.commit()
        except Exception:conn.rollback();raise
        finally:conn.close()
    def add(self,state,message,target_id=''):
        with self.connection() as conn:
            conn.execute('INSERT INTO events(created,state,target_id,message) VALUES(?,?,?,?)',(time.time(),state,target_id,str(message)[:1000]))
            conn.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 5000)')
    def list(self):
        with self.connection() as conn:return [dict(row) for row in conn.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]


class UpdateChecker:
    def __init__(self,service):self.service=service
    def _trusted_release(self,channel):
        service=self.service;policy=service.require_policy()
        from tuf.ngclient import Updater
        from tuf.ngclient.config import UpdaterConfig
        directory=service.directory/'metadata'/channel;directory.mkdir(parents=True,exist_ok=True)
        updater=Updater(str(directory),policy.metadata_url,str(service.directory/'downloads'),policy.targets_url,
            fetcher=progress_fetcher(service.fetcher or public_fetcher(policy),service._download_progress),config=UpdaterConfig(max_root_rotations=32,max_delegations=8),bootstrap=policy.bootstrap)
        updater.refresh()
        target=updater.get_targetinfo(f'{PRODUCT_ID}/{channel}/SeedanceStudio_Setup.exe')
        if target is None:raise UpdateRejected('受信任仓库尚无当前产品与通道的更新')
        release=validate_release(target,policy=policy,channel=channel,current_version=service.current_version,
                                 current_schema=service.current_schema,**service.platform_facts)
        return updater,target,release
    def check(self,channel='stable'):
        service=self.service
        with service._lock:
            assert_admission_open(service.home)
            updater,target,release=self._trusted_release(channel)
            identity=release.identity()
            with service.history_store.connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                previous=conn.execute('SELECT sequence,identity FROM highest WHERE channel=?',(channel,)).fetchone()
                if previous and (release.sequence<previous['sequence'] or (release.sequence==previous['sequence'] and previous['identity']!=identity)):
                    raise UpdateRejected('检测到版本回滚或相同序列的更新包重放冲突')
                conn.execute('INSERT INTO highest VALUES(?,?,?) ON CONFLICT(channel) DO UPDATE SET sequence=excluded.sequence,identity=excluded.identity',(channel,release.sequence,identity))
                found=conn.execute('SELECT id FROM releases WHERE identity=?',(identity,)).fetchone()
                identifier=found['id'] if found else uuid.uuid4().hex
                if not found:conn.execute('INSERT INTO releases VALUES(?,?,?,?,?)',(identifier,identity,json.dumps(release.__dict__),None,'available'))
            if release.version==service.current_version and release.build==service.current_build:
                return {'state':'up_to_date','version':release.version,'build':release.build}
            service.history_store.add('available','已验证 TUF 元数据；安装包仍需下载及发布者验证',identifier)
            return {'target_id':identifier,**release.__dict__,'installer_verified':False}


class UpdateDownloader:
    def __init__(self,service):self.service=service
    def download(self,target_id):
        service=self.service
        with service._lock:
            assert_admission_open(service.home)
            release,_=service.release(target_id)
            updater,target,current=service.checker._trusted_release(release.channel)
            if current.identity()!=release.identity():raise UpdateRejected('更新已变化，请重新检查，不沿用旧下载授权')
            directory=service.directory/'downloads';directory.mkdir(exist_ok=True)
            partial=directory/(target_id+'.partial')
            final=directory/(target_id+'.exe')
            service.history_store.add('downloading','开始下载已签名元数据绑定的安装包',target_id)
            try:
                updater.download_target(target,filepath=str(partial))
                verify_package(partial,release)
                # Downloads may be useful before publisher provisioning, but no
                # package becomes executable until the independent verifier runs.
                os.replace(partial,final)
                with service.history_store.connection() as conn:conn.execute("UPDATE releases SET path=?,state='downloaded' WHERE id=?",(str(final),target_id))
                service.history_store.add('downloaded','安装包长度及 SHA256 验证通过',target_id)
                return {'target_id':target_id,'state':'downloaded','bytes':release.length}
            except Exception:
                partial.unlink(missing_ok=True);service.history_store.add('download_failed','下载或完整性校验失败，禁止安装',target_id);raise


class UpdateVerifier:
    def __init__(self,service):self.service=service
    def verify(self,target_id):
        service=self.service;policy=service.require_policy()
        release,path=service.release(target_id)
        if not path:raise UpdateBlocked('请先下载受信任更新')
        _,_,current=service.checker._trusted_release(release.channel)
        if current.identity()!=release.identity():raise UpdateRejected('更新元数据已经变化')
        package=verify_package(path,release)
        signer=service.signature_verifier(package,policy.signer_sha256)
        return release,package,signer


class UpdateInstaller:
    def __init__(self,service):self.service=service
    def prepare(self,target_id,*,confirmed=False,stop_file=None,parent_pid=None,parent_birthstamp=None,install_root=None,program_root=None):
        service=self.service
        if confirmed is not True:raise UpdateBlocked('需要用户明确确认安装，不会强制关闭工作台')
        release,package,signer=service.verifier.verify(target_id)
        identifier=uuid.uuid4().hex
        control=service.control_path;control.parent.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(control,timeout=15)
        database_connections=[]
        try:
            conn.execute('BEGIN IMMEDIATE');assert_admission_open(service.home,conn)
            _table_states(conn)
            conn.execute('INSERT INTO studio_update_gate VALUES(1,?,?,?)',(identifier,'preparing',time.time()))
            # Publish the durable gate before lengthy snapshots/copying. New
            # admissions then see it in their own serialized control transaction.
            conn.commit()
            conn.execute('BEGIN IMMEDIATE')
            files=database_files(service.home)
            for file in files:
                if file==control:continue
                db=sqlite3.connect(file,timeout=15);db.execute('BEGIN IMMEDIATE');_table_states(db);database_connections.append(db)
            directory=service.directory/'transactions'/identifier;directory.mkdir(parents=True)
            snapshots=[]
            for index,file in enumerate(files):
                if file==control:continue
                destination=directory/(str(index)+'.snapshot.db')
                snapshot_database(file,destination)
                snapshots.append({'source':str(file),'snapshot':str(destination),'sha256':sha256(destination),'business_fingerprint':business_fingerprint(file)})
            # Credentials stay encrypted. Preserve same-user bytes without ever
            # printing them or placing them in a public diagnostics export.
            profiles=[]
            for name in ('configuration.json','profile.json','output-roots.json','secrets.dpapi','provider_secrets.dpapi'):
                file=plain_path(service.home/name)
                if file.is_file():
                    snapshot=directory/(name+'.backup');snapshot.write_bytes(file.read_bytes())
                    profiles.append({'source':str(file),'snapshot':str(snapshot),'sha256':sha256(snapshot)})
            if not stop_file or not parent_pid or not parent_birthstamp or not install_root:
                raise UpdateBlocked('缺少受控桌面实例身份，不能执行安装或关闭其他进程')
            stop=plain_path(stop_file)
            if not stop.is_relative_to(service.home/'desktop-sessions'):
                raise UpdateRejected('停止信号必须属于当前应用桌面会话')
            install=plain_path(install_root)
            if install.is_relative_to(service.home) or service.home.is_relative_to(install):
                raise UpdateRejected('程序目录和更新用户数据目录必须独立')
            program=plain_path(program_root) if program_root else install
            versioned=program!=install
            if versioned and (program.parent!=install/'versions' or program.name!=service.current_version+'-'+service.current_build):
                raise UpdateRejected('当前程序目录与受控版本安装布局不一致')
            if versioned and program.name==release.version+'-'+release.build:
                raise UpdateRejected('更新不能覆盖正在使用的相同版本目录')
            if versioned and (install/'versions'/(release.version+'-'+release.build)).exists():
                raise UpdateBlocked('目标版本目录已存在，保留原文件；请先核实以前的安装或恢复结果')
            runner,runner_files,runner_bytes=self.stage_runner(program,identifier)
            plan={'format':'seedance.update-plan@1','id':identifier,'product_id':PRODUCT_ID,'home':str(service.home),
                'target_id':target_id,'release':release.__dict__,'package':str(package),'publisher':signer,
                'state':'prepared','created':time.time(),'stop_file':str(stop),'parent_pid':int(parent_pid),
                'parent_birthstamp':str(parent_birthstamp),'install_root':str(install),
                'program_root':str(program),'versioned_layout':versioned,
                'runner':str(runner),'runner_files':runner_files,'runner_bytes':runner_bytes,
                'snapshots':snapshots,'profiles':profiles,'current_version':service.current_version,
                'current_build':service.current_build,'current_schema':service.current_schema}
            plan_path=service.directory/'plans'/(identifier+'.json');atomic_json(plan_path,plan)
            conn.execute("UPDATE studio_update_gate SET state='prepared',updated=? WHERE slot=1",(time.time(),));conn.commit()
            service.history_store.add('prepared','新任务已暂停，事务快照与更新计划已持久化',target_id)
            return {'transaction_id':identifier,'state':'prepared','plan':str(plan_path),
                    'helper_exe':str(runner),'runner_bytes':runner_bytes}
        except Exception:
            conn.rollback()
            _gate_schema(conn)
            conn.execute('DELETE FROM studio_update_gate WHERE transaction_id=?',(identifier,));conn.commit()
            raise
        finally:
            for db in database_connections:db.rollback();db.close()
            conn.close()
    def stage_runner(self,install_root,identifier):
        """Copy/verify the current onedir so its helper locks no installed files."""
        service=self.service;source=plain_path(install_root)
        executable=source/'SeedanceStudio.exe'
        if not executable.is_file():raise UpdateRejected('当前完整桌面程序目录无效')
        # The currently installed publisher must also match the provisioned
        # allowlist. Tests inject an explicitly separate verifier, never UI data.
        service.signature_verifier(executable,service.require_policy().signer_sha256)
        files=[];size=0
        for folder,dirs,names in os.walk(source,followlinks=False):
            for name in dirs+names:plain_path(Path(folder)/name)
            for name in names:
                file=Path(folder)/name;files.append(file);size+=file.stat().st_size
                if len(files)>100000:raise UpdateRejected('安装目录文件数超过安全范围')
        if shutil.disk_usage(service.home).free<size*2+256*1024*1024:
            raise UpdateBlocked('安全更新临时空间不足；需要完整独立 helper 副本，不会覆盖当前程序')
        destination=service.directory/'runners'/identifier;destination.mkdir(parents=True)
        manifest=[];started=time.monotonic()
        for file in files:
            if time.monotonic()-started>600:raise TimeoutError('受控更新 helper 复制超过十分钟')
            relative=file.relative_to(source);copy=destination/relative
            _native(copy.parent).mkdir(parents=True,exist_ok=True)
            before=file.stat();shutil.copy2(_native(file),_native(copy));after=file.stat()
            original_hash=sha256(_native(file))
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or sha256(_native(copy))!=original_hash:
                raise UpdateRejected('更新 helper 复制时源文件变化或哈希不一致')
            manifest.append({'path':relative.as_posix(),'sha256':original_hash,'size':after.st_size})
        return destination/'SeedanceStudio.exe',manifest,size


class UpdateRecovery:
    def __init__(self,service):self.service=service
    def restore(self,plan):
        # Check every DB before replacing any. Changes after upgrade are never
        # silently discarded, even if they appear operational rather than paid.
        known={plain_path(item['source']) for item in plan['snapshots']}|{self.service.control_path}
        for file in database_files(self.service.home):
            if file not in known and business_fingerprint(file)!=hashlib.sha256().hexdigest():
                raise UpdateBlocked('升级后出现新的业务数据库，保留新数据，禁止自动覆盖回滚')
        for item in plan['snapshots']:
            source=plain_path(item['source']);backup=plain_path(item['snapshot'])
            if not source.is_relative_to(self.service.home/'data') or not backup.is_relative_to(self.service.directory/'transactions'/plan['id']):
                raise UpdateRejected('恢复快照路径越界')
            if sha256(backup)!=item['sha256'] or business_fingerprint(source)!=item['business_fingerprint']:
                raise UpdateBlocked('升级后的业务数据已变化或快照损坏，已保留新数据和旧快照；禁止自动覆盖回滚')
        for item in plan.get('profiles',[]):
            source=plain_path(item['source']);backup=plain_path(item['snapshot'])
            if (source.parent!=self.service.home or sha256(backup)!=item['sha256']
                    or not source.is_file() or sha256(source)!=item['sha256']):
                raise UpdateBlocked('升级后的配置或凭据已变化，保留新配置和旧快照，禁止覆盖回滚')
        preserved=[]
        for item in plan['snapshots']:
            source=Path(item['source']);backup=Path(item['snapshot'])
            destination=source.with_name(source.name+'.update-restore-'+uuid.uuid4().hex[:8])
            snapshot_database(backup,destination)
            for suffix in ('-wal','-shm'):
                file=Path(str(source)+suffix)
                if file.exists():file.rename(file.with_name(file.name+'.pre-restore-'+uuid.uuid4().hex[:8]))
            saved=source.with_name(source.name+'.pre-restore-'+uuid.uuid4().hex[:8]);source.rename(saved)
            os.replace(destination,source);preserved.append(str(saved))
        for item in plan.get('profiles',[]):
            source=plain_path(item['source']);backup=plain_path(item['snapshot'])
            if source.parent!=self.service.home or sha256(backup)!=item['sha256']:
                raise UpdateRejected('配置恢复证据无效')
            if source.exists():source.rename(source.with_name(source.name+'.pre-restore-'+uuid.uuid4().hex[:8]))
            source.write_bytes(backup.read_bytes())
        self.service.history_store.add('data_restored','已恢复未产生后续业务写入的独立快照；程序回退仍须受信任旧安装器',plan['target_id'])
        return {'state':'data_restored','preserved_new_databases':preserved,'old_program_restored':False}


class UpdateService:
    def __init__(self,home,resource_root,*,current_version,current_build,current_schema,policy=None,
                 fetcher=None,signature_verifier=None,platform_facts=None,control_path=None):
        self.home=plain_path(home);self.resource_root=plain_path(resource_root)
        self.current_version=current_version;self.current_build=current_build;self.current_schema=current_schema
        self._policy=policy;self.fetcher=fetcher;self.signature_verifier=signature_verifier or verify_authenticode
        self.platform_facts=platform_facts or {};self._lock=threading.RLock()
        self._job_lock=threading.Lock();self._job={'state':'idle','action':None,'target_id':None,'downloaded_bytes':0,'total_bytes':None,'error':None,'result':None}
        self.history_store=UpdateHistory(self.home);self.directory=self.history_store.directory
        self.control_path=plain_path(control_path) if control_path else control_file(self.home)
        self.checker=UpdateChecker(self);self.downloader=UpdateDownloader(self);self.verifier=UpdateVerifier(self)
        self.installer=UpdateInstaller(self);self.recovery=UpdateRecovery(self)
    def require_policy(self):
        policy=self._policy or load_bundled_policy(self.resource_root)
        if policy is None:raise UpdateBlocked('正式更新源、TUF 信任根与发布签名尚未配置；线上更新 BLOCKED，不会联网或安装')
        return policy
    def status(self):
        try:self.require_policy();state='ready';reason=None
        except (UpdateBlocked,UpdateRejected) as exc:state='BLOCKED';reason=str(exc)
        with self._job_lock:job=dict(self._job)
        maintenance=None
        if self.control_path.exists():
            with sqlite3.connect(self.control_path,timeout=5) as connection:
                table=connection.execute("SELECT 1 FROM sqlite_master WHERE name='studio_update_gate' AND type='table'").fetchone()
                row=connection.execute('SELECT transaction_id,state FROM studio_update_gate WHERE slot=1').fetchone() if table else None
                if row:maintenance={'transaction_id':row[0],'state':row[1]}
        return {'state':state,'reason':reason,'current_version':self.current_version,'current_build':self.current_build,
                'maintenance':maintenance,
                'job':job,
                'channels':['stable','beta'],'automatic_check':True,'automatic_install':False,'history':self.history_store.list()}
    def release(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise UpdateRejected('更新标识无效')
        with self.history_store.connection() as conn:row=conn.execute('SELECT * FROM releases WHERE id=?',(identifier,)).fetchone()
        if row is None:raise UpdateRejected('更新记录不存在，请重新检查')
        release=Release(**json.loads(row['release_json']))
        path=plain_path(row['path']) if row['path'] else None
        if path and path.parent!=self.directory/'downloads':raise UpdateRejected('更新安装包路径越界')
        return release,path
    def check(self,channel='stable'):return self.checker.check(channel)
    def download(self,identifier):return self.downloader.download(identifier)
    def prepare_install(self,identifier,**kwargs):
        with self._lock:return self.installer.prepare(identifier,**kwargs)
    def history(self):return self.history_store.list()
    def _download_progress(self,url,count):
        with self._job_lock:
            if self._job['action']=='download' and self._job['state']=='running' and '/targets/' in url:
                self._job['downloaded_bytes']=count
    def _begin_job(self,action,callback,target_id=None):
        self.require_policy()
        with self._job_lock:
            if self._job['state']=='running':raise UpdateBlocked('已有更新操作进行中，请等待完成')
            total=self.release(target_id)[0].length if target_id else None
            self._job={'state':'running','action':action,'target_id':target_id,'downloaded_bytes':0,'total_bytes':total,'error':None,'result':None}
        def worker():
            try:
                result=callback()
                with self._job_lock:self._job.update(state='completed',result=result)
            except Exception as exc:
                with self._job_lock:self._job.update(state='failed',error=str(exc))
                self.history_store.add(action+'_failed',type(exc).__name__,target_id or '')
        threading.Thread(target=worker,name='trusted-update-'+action,daemon=True).start()
        return self.status()
    def begin_check(self,channel='stable'):return self._begin_job('check',lambda:self.check(channel))
    def begin_download(self,identifier):return self._begin_job('download',lambda:self.download(identifier),identifier)
    def begin_prepare_install(self,identifier,**kwargs):return self._begin_job('prepare',lambda:self.prepare_install(identifier,**kwargs),identifier)
    def begin_install(self,identifier,*,on_prepared,**kwargs):
        """Only backend code supplies this callback; HTTP JSON never selects it."""
        if not callable(on_prepared):raise UpdateBlocked('尚未接入受控桌面 helper 启动器')
        def prepare_and_launch():
            result=self.prepare_install(identifier,**kwargs)
            try:
                launched=on_prepared(result)
                return {**result,'state':'helper_started','helper':launched}
            except Exception:
                self.cancel_prepared(result['transaction_id'])
                raise
        return self._begin_job('install',prepare_and_launch,identifier)
    def cancel_prepared(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise UpdateRejected('更新事务标识无效')
        file=plain_path(self.directory/'plans'/(identifier+'.json'))
        plan=json.loads(file.read_text('utf8'))
        conn=sqlite3.connect(self.control_path,timeout=15)
        try:
            conn.execute('BEGIN IMMEDIATE');_gate_schema(conn)
            row=conn.execute('SELECT transaction_id,state FROM studio_update_gate WHERE slot=1').fetchone()
            if not row or row!=(identifier,'prepared') or plan.get('state')!='prepared':raise UpdateBlocked('更新已被独立 helper 接管，不能直接取消')
            plan['state']='cancelled';atomic_json(file,plan)
            conn.execute('DELETE FROM studio_update_gate WHERE transaction_id=?',(identifier,));conn.commit()
        finally:conn.close()
        self.history_store.add('cancelled','用户在安装前取消，已恢复接收新任务',plan['target_id'])
        return {'state':'cancelled'}
    def start_checks(self,stop_event,interval_seconds=21600):
        if not 300<=interval_seconds<=604800:raise ValueError('更新检查周期超出安全范围')
        def worker():
            while not stop_event.is_set():
                try:
                    if self.status()['state']=='BLOCKED':return
                    self.check()
                except Exception as exc:self.history_store.add('check_failed',type(exc).__name__)
                if stop_event.wait(interval_seconds):return
        thread=threading.Thread(target=worker,name='trusted-update-check',daemon=True);thread.start();return thread
