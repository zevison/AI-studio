"""Opt-in OSS reference upload with unique object identity and byte integrity.

Never reuses an object based only on its filename or length, and never runs
the legacy bucket-wide cleanup routine while preparing a Hypit quote.
"""
import base64,hashlib,json,mimetypes,os,re,time,uuid
from datetime import timedelta
from pathlib import Path
from .hypit_openlux_gateway import _external_https


def _save_journal(path, record):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf8') as stream:
        json.dump(record,stream,sort_keys=True)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def cleanup_owned_versions(journal_dir,*,client,sdk,cfg,now=None):
    """Delete at most 20 expired, positively versioned objects from our journal.

    Unversioned or ambiguous objects are retained: a HEAD followed by an
    unconditional DELETE cannot exclude another writer changing the object.
    """
    now=time.time() if now is None else now
    result={'deleted':0,'retained':0,'errors':0}
    prefix=(cfg.get('prefix') or 'seedance-studio').rstrip('/')+'/hypit-assets/'
    for path in sorted(Path(journal_dir).glob('*.json'))[:200]:
        if result['deleted']+result['errors']>=20:break
        try:
            if path.is_symlink() or path.stat().st_size>16384:continue
            record=json.loads(path.read_text(encoding='utf8'))
            if (record.get('format')!='seedance.hypit-upload@1' or record.get('phase')=='deleted'
                or record.get('endpoint')!=cfg['endpoint'] or record.get('bucket')!=cfg['bucket']
                or float(record['cleanup_after'])>now):continue
            version=record.get('version_id');key=record['object_key']
            if not re.fullmatch(re.escape(prefix)+r'[a-f0-9]{64}/[a-f0-9]{32}\.[a-z0-9]+',key):
                result['retained']+=1;continue
            if not version or version=='null':
                result['retained']+=1;continue
            head=client.head_object(sdk.HeadObjectRequest(bucket=cfg['bucket'],key=key,version_id=version))
            if (str(head.etag).strip('"').lower()!=record['expected_etag'] or
                (head.metadata or {}).get('sha256')!=record['sha256']):
                result['retained']+=1;continue
            client.delete_object(sdk.DeleteObjectRequest(bucket=cfg['bucket'],key=key,version_id=version))
            record.update(phase='deleted',deleted_at=now);_save_journal(path,record)
            result['deleted']+=1
        except Exception:
            # Cleanup is isolated from quote preparation; preserve diagnostic
            # identity and never retry an ambiguous deletion in this pass.
            result['errors']+=1
    return result


def upload_reference(asset,*,client=None,sdk=None,cfg=None,journal_dir=None):
    if client is None:
        from . import oss
        sdk,cfg=oss.oss_sdk,oss._cfg()
    if not sdk or not re.fullmatch(r'https://oss-[a-z0-9-]+\.aliyuncs\.com',cfg['endpoint']):
        raise PermissionError('Hypit reference upload requires a verified public Alibaba OSS endpoint')
    if client is None:
        client=oss._client(bounded=True)
    if journal_dir is None:
        from . import config
        journal_dir=config.DATA_DIR/'hypit_reference_uploads'
    journal_dir=Path(journal_dir)
    if journal_dir.is_symlink():raise PermissionError('Upload journal must be a regular directory')
    journal_dir.mkdir(parents=True,exist_ok=True)
    cleanup_owned_versions(journal_dir,client=client,sdk=sdk,cfg=cfg)
    file=Path(asset['path'])
    if file.is_symlink() or not file.is_file() or not 0<file.stat().st_size<=400*1024**2:
        raise PermissionError('Bounded regular reference file required')
    sha,md5=hashlib.sha256(),hashlib.md5()
    with file.open('rb') as stream:
        before=file.stat()
        while chunk:=stream.read(1024*1024):sha.update(chunk);md5.update(chunk)
        if sha.hexdigest()!=asset['sha256']:raise PermissionError('Reference bytes changed before upload')
        key=(cfg.get('prefix') or 'seedance-studio').rstrip('/')+'/hypit-assets/'+sha.hexdigest()+'/'+uuid.uuid4().hex+file.suffix.lower()
        record={'format':'seedance.hypit-upload@1','phase':'upload_uncertain',
            'endpoint':cfg['endpoint'],'bucket':cfg['bucket'],'object_key':key,
            'sha256':sha.hexdigest(),'expected_etag':md5.hexdigest(),'created_at':time.time(),
            'cleanup_after':time.time()+24*3600}
        journal=journal_dir/(uuid.uuid4().hex+'.json')
        _save_journal(journal,record)
        stream.seek(0)
        request=sdk.PutObjectRequest(bucket=cfg['bucket'],key=key,body=stream,
            content_length=before.st_size,content_md5=base64.b64encode(md5.digest()).decode('ascii'),
            content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream',
            metadata={'sha256':sha.hexdigest()},forbid_overwrite=True)
        response=client.put_object(request)
        record.update(phase='uploaded_unverified',etag=str(getattr(response,'etag','')).strip('"').lower(),
                      version_id=getattr(response,'version_id',None))
        _save_journal(journal,record)
        after=file.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise PermissionError('Reference changed during upload; no video submission allowed')
    etag=str(getattr(response,'etag','')).strip('"').lower()
    if etag!=md5.hexdigest():raise PermissionError('OSS acknowledgement did not prove uploaded byte integrity')
    version=getattr(response,'version_id',None)
    query=sdk.GetObjectRequest(bucket=cfg['bucket'],key=key,**({'version_id':version} if version and version!='null' else {}))
    signed=client.presign(query,expires=timedelta(hours=2))
    if getattr(signed,'signed_headers',None):raise PermissionError('Provider cannot send additional signed media headers')
    url=_external_https(signed.url,'reference object')
    record.update(phase='ready',expires_at=time.time()+7200)
    _save_journal(journal,record)
    return {'url':url,'sha256':sha.hexdigest(),'size':before.st_size,'object_key':key,'etag':etag,
            'version_id':version,'object_identity':'unique key, overwrite forbidden; explicit version when bucket supports it',
            'expires_in':7200}
