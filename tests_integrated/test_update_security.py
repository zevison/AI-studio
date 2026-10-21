"""Actual Ed25519-signed TUF metadata; no network or production signing key."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata,Root,Targets,Snapshot,Timestamp,TargetFile,MetaFile

from core.update_security import (TrustPolicy,UpdateRejected,UpdateBlocked,load_bundled_policy,
    local_repository_fetcher,verify_package,validate_release,verify_authenticode)
from core.update_service import UpdateService


class SignedRepository:
    def __init__(self,directory):
        self.root=directory;self.metadata=directory/'metadata';self.targets=directory/'targets'
        self.metadata.mkdir(parents=True);self.targets.mkdir()
        self.keys={name:CryptoSigner.generate_ed25519() for name in ('root','targets','snapshot','timestamp')}
        root=Root(version=1,expires=self.expiry(),consistent_snapshot=False)
        for name,key in self.keys.items():root.add_key(key.public_key,name)
        envelope=Metadata(root);envelope.sign(self.keys['root']);self.bootstrap=envelope.to_bytes()
        (self.metadata/'1.root.json').write_bytes(self.bootstrap)
        self.payload=b'Local controlled installer payload, never executed by protocol tests'
        self.custom={'product_id':'SeedanceStudio','channel':'stable','version':'5.2.1','build':'R4-local-test',
            'release_sequence':2,'minimum_app_version':'5.2.0','platform':'windows-x64','minimum_windows_build':19041,
            'schema_min':390,'schema_max':391,'schema_new':391,'notes':'Local cryptographic protocol test'}
        self.metadata_version=1
        self.publish()
    @staticmethod
    def expiry(days=2):return datetime.now(timezone.utc)+timedelta(days=days)
    def publish(self,*,expired=False):
        name='SeedanceStudio/stable/SeedanceStudio_Setup.exe'
        target=TargetFile.from_data(name,self.payload,['sha256'])
        target.unrecognized_fields['custom']=dict(self.custom)
        targets=Metadata(Targets(version=self.metadata_version,expires=self.expiry(),targets={name:target}))
        targets.sign(self.keys['targets']);raw_targets=targets.to_bytes()
        snapshot=Metadata(Snapshot(version=self.metadata_version,expires=self.expiry(),meta={
            'targets.json':MetaFile(self.metadata_version,len(raw_targets),{'sha256':hashlib.sha256(raw_targets).hexdigest()})}))
        snapshot.sign(self.keys['snapshot']);raw_snapshot=snapshot.to_bytes()
        timestamp=Metadata(Timestamp(version=self.metadata_version,expires=self.expiry(-1 if expired else 2),snapshot_meta=
            MetaFile(self.metadata_version,len(raw_snapshot),{'sha256':hashlib.sha256(raw_snapshot).hexdigest()})))
        timestamp.sign(self.keys['timestamp'])
        (self.metadata/'targets.json').write_bytes(raw_targets)
        (self.metadata/'snapshot.json').write_bytes(raw_snapshot)
        (self.metadata/'timestamp.json').write_bytes(timestamp.to_bytes())
        destination=self.targets/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(self.payload)
        return target
    def policy(self):return TrustPolicy(self.bootstrap,'https://local-updates.invalid/metadata/','https://local-updates.invalid/targets/',('a'*64,))
    def service(self,home,**kwargs):
        policy=self.policy()
        return UpdateService(home,self.root,current_version='5.2.0',current_build='R3.9-test',current_schema=390,
            policy=policy,fetcher=local_repository_fetcher(self.root,policy),
            platform_facts={'os_name':'Windows','architecture':'AMD64','os_build':22631},**kwargs)


@pytest.fixture
def repository(tmp_path):return SignedRepository(tmp_path/'signed-repository')


def test_real_ed25519_tuf_check_and_payload_download(repository,tmp_path):
    service=repository.service(tmp_path/'user')
    found=service.check()
    assert found['version']=='5.2.1' and found['installer_verified'] is False
    saved=service.download(found['target_id'])
    release,path=service.release(found['target_id'])
    assert saved['bytes']==len(repository.payload)
    assert path.read_bytes()==repository.payload
    verify_package(path,release)
    assert service.status()['automatic_install'] is False


def test_no_production_root_means_no_network_and_no_execution(tmp_path,monkeypatch):
    service=UpdateService(tmp_path/'user',tmp_path/'resources',current_version='5.2.0',current_build='R3.9',current_schema=390)
    assert service.status()['state']=='BLOCKED'
    with pytest.raises(UpdateBlocked):service.begin_check()
    with pytest.raises(UpdateBlocked):service.check()
    assert not (tmp_path/'user/updates/metadata').exists()


@pytest.mark.parametrize('field,value',[
    ('product_id','AIProductStudio'),('channel','beta'),('version','5.1.0'),
    ('minimum_app_version','5.3.0'),('platform','linux-x64'),('minimum_windows_build',99999),
    ('schema_min',391),('schema_max',389),('schema_new',389),('release_sequence',0)])
def test_signed_metadata_still_must_meet_application_policy(repository,tmp_path,field,value):
    repository.custom[field]=value;repository.publish()
    with pytest.raises(UpdateRejected):repository.service(tmp_path/'user').check()


def test_expired_tuf_timestamp_rejected(repository,tmp_path):
    repository.publish(expired=True)
    with pytest.raises(Exception,match='expired|Expired'):
        repository.service(tmp_path/'user').check()


def test_tampered_metadata_signature_rejected(repository,tmp_path):
    file=repository.metadata/'targets.json';doc=json.loads(file.read_text('utf8'))
    doc['signed']['targets']['SeedanceStudio/stable/SeedanceStudio_Setup.exe']['custom']['version']='99.0.0'
    file.write_text(json.dumps(doc),encoding='utf8')
    with pytest.raises(Exception):repository.service(tmp_path/'user').check()


def test_untrusted_signing_key_rejected(repository,tmp_path):
    file=repository.metadata/'timestamp.json';doc=Metadata.from_file(str(file))
    doc.sign(CryptoSigner.generate_ed25519());file.write_bytes(doc.to_bytes())
    with pytest.raises(Exception):repository.service(tmp_path/'user').check()


def test_download_rejects_tampered_signed_size_and_hash(repository,tmp_path):
    service=repository.service(tmp_path/'user');found=service.check()
    (repository.targets/'SeedanceStudio/stable/SeedanceStudio_Setup.exe').write_bytes(b'evil')
    with pytest.raises(Exception):service.download(found['target_id'])
    assert not list((service.directory/'downloads').glob('*.exe'))


def test_signed_release_sequence_cannot_roll_back_or_equivocate(repository,tmp_path):
    service=repository.service(tmp_path/'user');service.check()
    repository.metadata_version+=1;repository.custom['release_sequence']=1;repository.publish()
    with pytest.raises(UpdateRejected,match='回滚'):service.check()


def test_same_release_sequence_cannot_change_payload(repository,tmp_path):
    service=repository.service(tmp_path/'user');service.check()
    repository.metadata_version+=1;repository.payload=b'changed';repository.publish()
    with pytest.raises(UpdateRejected,match='重放冲突'):service.check()


def test_tuf_metadata_version_rollback_rejected(repository,tmp_path):
    original={file.name:file.read_bytes() for file in repository.metadata.glob('*.json')}
    service=repository.service(tmp_path/'user')
    repository.metadata_version=2;repository.publish();service.check()
    for name,raw in original.items():(repository.metadata/name).write_bytes(raw)
    with pytest.raises(Exception,match='version|Version|rollback'):
        service.check()


def test_wrong_publisher_blocks_even_correct_tuf_payload(repository,tmp_path):
    def rejected(*args):raise UpdateRejected('wrong publisher')
    service=repository.service(tmp_path/'user',signature_verifier=rejected)
    found=service.check();service.download(found['target_id'])
    with pytest.raises(UpdateRejected,match='publisher'):service.verifier.verify(found['target_id'])


def test_missing_allowlist_blocks_authenticode_without_process(tmp_path):
    with pytest.raises(UpdateBlocked):verify_authenticode(tmp_path/'any.exe',())


def test_bundled_trust_root_hash_is_checked_before_any_network(tmp_path):
    folder=tmp_path/'assets';folder.mkdir();(folder/'update-root.json').write_bytes(b'not trusted')
    (folder/'update-trust.json').write_text(json.dumps({'format':'seedance.update-trust@1','product_id':'SeedanceStudio',
        'enabled':True,'root_file':'update-root.json','root_sha256':'0'*64}),encoding='utf8')
    with pytest.raises(UpdateRejected,match='哈希'):load_bundled_policy(tmp_path)
