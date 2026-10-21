"""Real local Ed25519/TUF protocol tests; native payload health uses explicit fixtures."""
import hashlib
import json
from pathlib import Path
import pytest
from tuf.api.metadata import Metadata,Targets,TargetFile,Snapshot,Timestamp,MetaFile
from test_update_security import SignedRepository
from core import component_updates as updates
from core import component_manager as manager
from core.update_security import local_repository_fetcher,UpdateBlocked,UpdateRejected


def fixture(tmp_path):
    root=tmp_path/'resources';(root/'tools').mkdir(parents=True);(root/'assets').mkdir()
    def item(version,data):
        files=[{'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'url':'https://example.invalid/'+name} for name in ('config.json','model.bin','tokenizer.json','vocabulary.txt')]
        return dict(id='asr',title='Controlled protocol fixture',version=version,display_version=version,
                    os='windows',architecture='x64',rollback='versioned-retained',app_minimum='5.2.0',app_maximum_exclusive='6.0.0',
                    install_type='model-files',health_check='load-small-cpu-int8',dependencies=['core.asr-engine'],
                    installed_bytes=4*len(data),files=files,installed_files={r['path']:r['sha256'] for r in files})
    old=item('old',b'old');new=item('new',b'new')
    (root/'tools/components-profile.json').write_text(json.dumps({'format':'seedance.components@2','host_version':'5.2.0','components':{'asr':old}}),'utf8')
    repo=SignedRepository(tmp_path/'signed-plugin-repository');trust=repo.policy()
    (root/'assets/plugin-update-root.json').write_bytes(trust.bootstrap)
    (root/'assets/plugin-update-trust.json').write_text(json.dumps({'format':'seedance.plugin-update-trust@1','product_id':'SeedanceStudio',
        'enabled':True,'root_file':'plugin-update-root.json','root_sha256':hashlib.sha256(trust.bootstrap).hexdigest(),
        'metadata_url':trust.metadata_url,'targets_url':trust.targets_url}),'utf8')
    release={'format':'seedance.plugin-release@1','product_id':'SeedanceStudio','id':'asr','host_abi_sha256':updates.abi(root),
             'sequence':1,'notes':'Local signed metadata, not production service','component':new}
    return root,old,new,repo,trust,release


def publish(repo,release,*,expired=False):
    raw=json.dumps(release,sort_keys=True).encode();name='SeedanceStudio/plugins/'+release['id']+'.json'
    target=TargetFile.from_data(name,raw,['sha256'])
    targets=Metadata(Targets(version=repo.metadata_version,expires=repo.expiry(),targets={name:target}))
    targets.sign(repo.keys['targets']);targets_raw=targets.to_bytes()
    snapshot=Metadata(Snapshot(version=repo.metadata_version,expires=repo.expiry(),meta={
        'targets.json':MetaFile(repo.metadata_version,len(targets_raw),{'sha256':hashlib.sha256(targets_raw).hexdigest()})}))
    snapshot.sign(repo.keys['snapshot']);snapshot_raw=snapshot.to_bytes()
    timestamp=Metadata(Timestamp(version=repo.metadata_version,expires=repo.expiry(-1 if expired else 2),snapshot_meta=
        MetaFile(repo.metadata_version,len(snapshot_raw),{'sha256':hashlib.sha256(snapshot_raw).hexdigest()})))
    timestamp.sign(repo.keys['timestamp'])
    for name_file,data in [('targets.json',targets_raw),('snapshot.json',snapshot_raw),('timestamp.json',timestamp.to_bytes())]:
        (repo.metadata/name_file).write_bytes(data)
    file=repo.targets/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(raw)


def check(root,home,repo,trust):return updates.check(root,home,'asr',trust=trust,fetcher=local_repository_fetcher(repo.root,trust))


def install_fixture(home,key,item,data):
    folder=manager.location(home,key,item);folder.mkdir(parents=True)
    for row in item['files']:(folder/row['path']).write_bytes(data)
    return folder


def test_signed_check_install_selection_and_explicit_rollback(tmp_path):
    root,old,new,repo,trust,release=fixture(tmp_path);home=tmp_path/'home';publish(repo,release)
    found=check(root,home,repo,trust)
    assert found['version']=='new' and manager.catalog(root,home=home)['asr']==old
    old_folder=install_fixture(home,'asr',old,b'old')
    def download(row,cache,**_):
        cache.mkdir(parents=True,exist_ok=True);file=cache/row['sha256'];file.write_bytes(b'new');return file
    service=manager.ComponentManager(root,home,downloader=download,health_checker=lambda *_:{'available':True,'scope':'controlled fixture'})
    with pytest.raises(PermissionError):service.install_update('asr')
    service.install_update('asr',confirmed=True);service.thread.join(5)
    assert not service.thread.is_alive() and service.states['asr']['state']=='ready'
    assert manager.catalog(root,home=home)['asr']==new
    assert manager.resolve('asr',root=root,home=home)!=old_folder
    service.rollback_update('asr',confirmed=True)
    assert manager.catalog(root,home=home)['asr']==old
    assert (old_folder/'config.json').read_bytes()==b'old'
    assert manager.location(home,'asr',new).exists()


@pytest.mark.parametrize('mutation',['abi','architecture','sequence','path','hash','signature'])
def test_untrusted_or_incompatible_updates_never_replace_working_version(tmp_path,mutation):
    root,old,new,repo,trust,release=fixture(tmp_path);home=tmp_path/'home';install_fixture(home,'asr',old,b'old')
    if mutation=='abi':release['host_abi_sha256']='0'*64
    if mutation=='architecture':release['component']['architecture']='arm64'
    if mutation=='sequence':release['sequence']=0
    if mutation=='path':release['component']['installed_files']={'../outside':'a'*64}
    publish(repo,release)
    if mutation=='hash':(repo.targets/'SeedanceStudio/plugins/asr.json').write_bytes(b'changed')
    if mutation=='signature':
        raw=json.loads((repo.metadata/'targets.json').read_text('utf8'));raw['signed']['version']=9
        (repo.metadata/'targets.json').write_text(json.dumps(raw),'utf8')
    with pytest.raises(Exception):check(root,home,repo,trust)
    assert manager.catalog(root,home=home)['asr']==old
    assert manager.resolve('asr',root=root,home=home).is_dir()


def test_signed_sequence_cannot_roll_back(tmp_path):
    root,old,new,repo,trust,release=fixture(tmp_path);home=tmp_path/'home'
    release['sequence']=2;publish(repo,release);check(root,home,repo,trust)
    repo.metadata_version+=1;release['sequence']=1;publish(repo,release)
    with pytest.raises(UpdateRejected,match='回滚'):check(root,home,repo,trust)


def test_expired_online_metadata_cannot_admit_update(tmp_path):
    root,_,_,repo,trust,release=fixture(tmp_path);publish(repo,release,expired=True)
    with pytest.raises(Exception,match='expired|Expired'):check(root,tmp_path/'home',repo,trust)


def test_modified_cached_proof_is_rejected_and_base_catalog_survives(tmp_path):
    root,old,new,repo,trust,release=fixture(tmp_path);home=tmp_path/'home';publish(repo,release)
    result=check(root,home,repo,trust)
    install_fixture(home,'asr',new,b'new');updates.activate(root,home,'asr',new)
    file=updates.directory(home)/'proofs'/(result['proof_id']+'.json')
    raw=json.loads(file.read_text('utf8'));raw['release']=raw['release'].replace('"new"','"evil"')
    file.write_text(json.dumps(raw),'utf8')
    assert manager.catalog(root,home=home)['asr']==old
    assert updates.warning(root,home)


def test_default_missing_trust_does_not_use_network(tmp_path):
    root=tmp_path/'root';root.mkdir()
    assert updates.policy(root) is None
    with pytest.raises(UpdateBlocked):updates.check(root,tmp_path/'home','browser')


def test_root_rotation_uses_both_signatures_and_preserves_proof_chain(tmp_path):
    from securesystemslib.signer import CryptoSigner
    root,_,new,repo,trust,release=fixture(tmp_path)
    rotated=Metadata.from_bytes(repo.bootstrap);rotated.signed.version=2
    rotated.signed.revoke_key(repo.keys['root'].public_key.keyid,'root')
    key=CryptoSigner.generate_ed25519();rotated.signed.add_key(key.public_key,'root')
    rotated.sign(repo.keys['root']);rotated.sign(key,append=True)
    (repo.metadata/'2.root.json').write_bytes(rotated.to_bytes());publish(repo,release)
    result=check(root,tmp_path/'home',repo,trust)
    item,_=updates.load_proof(root,tmp_path/'home','asr',result['proof_id'])
    assert item==new
    proof=json.loads((updates.directory(tmp_path/'home')/'proofs'/(result['proof_id']+'.json')).read_text('utf8'))
    assert [p['signed']['version'] for p in proof['roots']]==[1,2]


def test_forged_proof_with_recomputed_file_hash_still_fails_signatures(tmp_path):
    root,old,new,repo,trust,release=fixture(tmp_path);home=tmp_path/'home';publish(repo,release)
    result=check(root,home,repo,trust)
    file=updates.directory(home)/'proofs'/(result['proof_id']+'.json')
    proof=json.loads(file.read_text('utf8'))
    proof['release']=proof['release'].replace('"new"','"evil"')
    raw=json.dumps(proof).encode();forged=hashlib.sha256(raw).hexdigest()
    (file.parent/(forged+'.json')).write_bytes(raw);updates._set(home,'active','asr',forged)
    assert manager.catalog(root,home=home)['asr']==old
    assert updates.warning(root,home)


def test_existing_signed_install_works_offline_after_expiry_but_new_admission_rejected(tmp_path,monkeypatch):
    from datetime import datetime,timedelta,timezone
    root,old,new,repo,trust,release=fixture(tmp_path);home=tmp_path/'home';publish(repo,release)
    result=check(root,home,repo,trust);install_fixture(home,'asr',new,b'new');updates.activate(root,home,'asr',new)
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.now(timezone.utc)+timedelta(days=10)
    monkeypatch.setattr(updates,'datetime',Future)
    assert updates.load_proof(root,home,'asr',result['proof_id'])[0]==new
    assert manager.catalog(root,home=home)['asr']==new
    with pytest.raises(UpdateRejected,match='过期'):updates.load_proof(root,home,'asr',result['proof_id'],fresh=True)


def test_corrupt_active_pointer_cannot_break_base_component_status(tmp_path):
    root,old,_,_,_,_=fixture(tmp_path);home=tmp_path/'home';directory=updates.directory(home);directory.mkdir(parents=True)
    (directory/'active.json').write_text('{broken','utf8')
    service=manager.ComponentManager(root,home)
    state=service.status()
    assert state['components'][0]['version']==old['version']
    assert state['plugin_updates']['catalog_warning']


def test_interrupted_candidate_pointer_write_cannot_admit_older_sequence(tmp_path):
    root,_,_,repo,trust,release=fixture(tmp_path);home=tmp_path/'home';publish(repo,release)
    old=check(root,home,repo,trust)
    repo.metadata_version+=1;release['sequence']=2;publish(repo,release);check(root,home,repo,trust)
    updates._set(home,'candidate','asr',old['proof_id'])  # Crash/replay left old pointer after highest advanced.
    service=manager.ComponentManager(root,home)
    with pytest.raises(UpdateRejected,match='发布序列'):service.install_update('asr',confirmed=True)
    assert service.thread is None
