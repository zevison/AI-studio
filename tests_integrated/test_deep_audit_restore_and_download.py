"""Atomic file publication, WAL backups and restore failure boundaries; offline."""
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
import pytest
from core import backup_manager,config,media,openlux

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import safe_migrate
import migration_bundle


class Response(io.BytesIO):
    def __init__(self,data,length=None):
        super().__init__(data);self.headers={} if length is None else {'Content-Length':str(length)}


def test_partial_download_preserves_existing_output(tmp_path,monkeypatch):
    output=tmp_path/'video.mp4';output.write_bytes(b'existing video')
    monkeypatch.setattr(openlux.urllib.request,'urlopen',lambda *a,**k:Response(b'partial',999))
    with pytest.raises(openlux.ApiError,match='不完整'):openlux.download('https://fixture/video',output)
    assert output.read_bytes()==b'existing video'
    assert not list(tmp_path.glob('.download-*'))


def test_undecodable_video_is_never_published(tmp_path,monkeypatch):
    monkeypatch.setattr(openlux.urllib.request,'urlopen',lambda *a,**k:Response(b'<html>gateway error</html>'))
    monkeypatch.setattr(media,'probe',lambda _: {'ok':False})
    with pytest.raises(openlux.ApiError,match='可解析'):openlux.download('https://fixture/video',tmp_path/'bad.mp4')
    assert not list(tmp_path.iterdir())


def test_decodable_complete_video_is_atomically_published(tmp_path,monkeypatch):
    data=b'local fixture video bytes'
    monkeypatch.setattr(openlux.urllib.request,'urlopen',lambda *a,**k:Response(data,len(data)))
    monkeypatch.setattr(media,'probe',lambda _: {'ok':True,'width':32,'height':32})
    output=tmp_path/'good.mp4';assert openlux.download('https://fixture/video',output)==output
    assert output.read_bytes()==data and not list(tmp_path.glob('.download-*'))


@pytest.mark.parametrize('response',[{'id':'other'},{'task':{'task_id':'other'}},{'data':{'id':'other'}}])
def test_query_identity_mismatch_never_updates_original(response):
    with pytest.raises(openlux.ApiError,match='不同'):openlux.validate_task_identity(response,'expected')


def installation(root):
    (root/'core').mkdir(parents=True);(root/'core/config.py').write_text("APP_VERSION = '5.2.0'",encoding='utf8')
    (root/'data/assets').mkdir(parents=True);(root/'data/outputs').mkdir()
    return root


def point_config(monkeypatch,root):
    for key,value in {'ROOT':root,'DATA_DIR':root/'data','DB_PATH':root/'data/studio.db','ASSET_DIR':root/'data/assets','OUTPUT_DIR':root/'data/outputs'}.items():
        monkeypatch.setattr(config,key,value)


def test_backup_preserves_product_sources_projects_and_auxiliary_wal(tmp_path,monkeypatch):
    root=installation(tmp_path/'old');point_config(monkeypatch,root)
    for directory,name in [('product_knowledge','source.txt'),('hypit_projects','main.svml')]:
        folder=root/'data'/directory/'id';folder.mkdir(parents=True);(folder/name).write_text('user-owned evidence')
    path=root/'data/hypit_token_grants/grant.sqlite';path.parent.mkdir()
    c=sqlite3.connect(path)
    try:
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0')
        c.execute('CREATE TABLE charges(usd TEXT)');c.execute("INSERT INTO charges VALUES('0.10')");c.commit()
        result=backup_manager.create_backup()
        with zipfile.ZipFile(result['path']) as z:
            assert 'data/product_knowledge/id/source.txt' in z.namelist()
            assert 'data/hypit_projects/id/main.svml' in z.namelist()
            assert not any(x.endswith(('-wal','-shm')) for x in z.namelist())
            restored=tmp_path/'ledger.sqlite';restored.write_bytes(z.read('data/hypit_token_grants/grant.sqlite'))
        check=sqlite3.connect(restored)
        try:assert check.execute('SELECT usd FROM charges').fetchone()[0]=='0.10'
        finally:check.close()
    finally:c.close()


def test_workspace_backup_uses_canonical_paths_and_excludes_host_env(tmp_path,monkeypatch):
    root=installation(tmp_path/'host');point_config(monkeypatch,root)
    root.joinpath('.env').write_text('HOST_PRIVATE_KEY=not-for-workspace')
    own=root/'data/workspaces/isolated'
    (own/'assets').mkdir(parents=True);(own/'assets/own.png').write_bytes(b'own file')
    monkeypatch.setattr(config,'DATA_DIR',own);monkeypatch.setattr(config,'DB_PATH',own/'studio.db')
    monkeypatch.setattr(config,'ASSET_DIR',own/'assets');monkeypatch.setattr(config,'OUTPUT_DIR',own/'outputs')
    monkeypatch.setattr(config,'WORKSPACE_ID','isolated')
    result=backup_manager.create_backup()
    with zipfile.ZipFile(result['path']) as z:
        assert 'data/assets/own.png' in z.namelist()
        assert '.env' not in z.namelist()
        assert not any('workspaces/' in x for x in z.namelist())


@pytest.mark.parametrize('table',['ai_messages','product_sources','radar_scripts','user_settings'])
def test_restore_never_overwrites_unlisted_user_tables(tmp_path,table):
    root=installation(tmp_path/'destination');path=root/'data/studio.db'
    with sqlite3.connect(path) as c:c.execute('CREATE TABLE '+table+'(value TEXT)');c.execute('INSERT INTO '+table+' VALUES(?)',('user history',))
    with pytest.raises(PermissionError,match='user data'):safe_migrate.database(path,fresh=True)
    with sqlite3.connect(path) as c:assert c.execute('SELECT value FROM '+table).fetchone()[0]=='user history'


def test_roundtrip_restore_rebases_materials_and_preserves_new_configuration(tmp_path,monkeypatch):
    old=installation(tmp_path/'old');point_config(monkeypatch,old)
    source=old/'data/assets/original.txt';source.write_text('material')
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute('CREATE TABLE assets(id TEXT,path TEXT)');c.execute('INSERT INTO assets VALUES(?,?)',('a',str(source)))
    archive=backup_manager.create_backup()['path']
    new=installation(tmp_path/'new');(new/'.env').write_text('OWN_EXISTING_CONFIGURATION=keep')
    point_config(monkeypatch,new);monkeypatch.setattr(migration_bundle,'ROOT',new)
    assert migration_bundle.do_import(Path(archive))==0
    assert (new/'.env').read_text()=='OWN_EXISTING_CONFIGURATION=keep'
    with sqlite3.connect(new/'data/studio.db') as c:
        restored=Path(c.execute('SELECT path FROM assets').fetchone()[0])
    assert restored==new/'data/assets/original.txt' and restored.read_text()=='material'
    assert source.read_text()=='material'


@pytest.mark.parametrize('name',['../escape','C:/escape','data/assets/a:stream','data/assets/CON','data/assets/x.'])
def test_unsafe_restore_paths_rejected_before_any_publication(tmp_path,name):
    archive=tmp_path/'hostile.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('backup_manifest.json',json.dumps({'version':'5.2.0','files':[name]}));z.writestr(name,b'bad')
    stage=tmp_path/'stage';stage.mkdir()
    with pytest.raises(ValueError):migration_bundle.extract_backup(archive,stage)
    assert not list(stage.iterdir())


def test_publish_failure_restores_destination_directory(tmp_path,monkeypatch):
    old=installation(tmp_path/'old');new=installation(tmp_path/'new')
    (old/'data/assets/source.txt').write_text('source preserved')
    (new/'data/keep.txt').write_text('destination preserved')
    rename=Path.rename
    def interrupted(path,target):
        if path.name.startswith('.migration-stage-') and Path(target)==new/'data':
            raise OSError('Injected publication interruption')
        return rename(path,target)
    monkeypatch.setattr(Path,'rename',interrupted)
    with pytest.raises(OSError):safe_migrate.migrate(old,new)
    assert (new/'data/keep.txt').read_text()=='destination preserved'
    assert (old/'data/assets/source.txt').read_text()=='source preserved'


def test_failure_after_publish_rolls_back_to_previous_data(tmp_path,monkeypatch):
    old=installation(tmp_path/'old');new=installation(tmp_path/'new')
    (old/'data/assets/source.txt').write_text('source preserved')
    (new/'data/keep.txt').write_text('destination preserved')
    write=Path.write_text;count=[]
    def interrupted(path,*args,**kwargs):
        if path.name.startswith('migration-'):
            count.append(1)
            if len(count)==2:raise OSError('Injected receipt failure after publication')
        return write(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',interrupted)
    with pytest.raises(OSError):safe_migrate.migrate(old,new)
    assert (new/'data/keep.txt').read_text()=='destination preserved'
    assert (old/'data/assets/source.txt').read_text()=='source preserved'


def test_audio_subject_connection_closes_after_operation(tmp_path,monkeypatch):
    from core import audio_subjects
    monkeypatch.setattr(audio_subjects,'DB',tmp_path/'audio_subjects.db')
    with audio_subjects._connect() as connection:
        connection.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
