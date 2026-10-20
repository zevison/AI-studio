"""A candidate can never inherit a successful stale privacy receipt."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import release_candidate as release


@pytest.fixture
def packet(tmp_path):
    root=tmp_path/'candidate';app=root/'Studio';docs=app/'docs';docs.mkdir(parents=True)
    (app/'app.py').write_text('print("fixture")\n')
    release.write(docs/'SOURCE_PROVENANCE.json',{'source_hashes':{'app.py':release.sha(app/'app.py')},'regression':{'python_passed':1}})
    return root


def approve(packet):
    release.write(packet/'Studio/docs/CREDENTIAL_SCAN.json',{'passed':True,'tree_sha256':release.tree_hash(packet)})


def test_stale_privacy_scan_refuses_build(packet):
    approve(packet);(packet/'Studio/app.py').write_text('changed after scan')
    with pytest.raises(ValueError,match='stale'):release.finalize(packet)
    assert not packet.with_suffix('.zip').exists()


def test_source_provenance_mismatch_refuses_build(packet):
    (packet/'Studio/app.py').write_text('source mismatch');approve(packet)
    with pytest.raises(ValueError,match='provenance'):release.finalize(packet)


def test_candidate_build_keeps_fresh_pc_gate_false_and_never_overwrites(packet):
    approve(packet);result=release.finalize(packet)
    assert result['candidate_only'] is True and result['fresh_pc_accepted'] is False
    assert result['crc_and_all_member_hashes_passed'] is True
    with pytest.raises(FileExistsError):release.finalize(packet)


def test_private_data_is_rejected_before_archive_creation(packet):
    (packet/'Studio/.env').write_text('FIXTURE=not-a-real-key');approve(packet)
    with pytest.raises(PermissionError):release.finalize(packet)
    assert not packet.with_suffix('.zip').exists()


@pytest.mark.parametrize('encoding',['utf8','utf-16-le','utf-16-be'])
def test_privacy_scanner_detects_credentials_in_log_encodings(packet,tmp_path,encoding):
    import scan_release_credentials as scanner
    fixture=tmp_path/'fixture_vault.py';vault=tmp_path/'fixture.key';vault.write_text('fixture marker')
    fixture.write_text('from pathlib import Path\nSECRETS_PATH=Path('+repr(str(vault))+')\ndef read_secrets():return {"api_key":"fixture-secret-for-scanner"}\n')
    (packet/'Studio/log.txt').write_bytes('fixture-secret-for-scanner'.encode(encoding))
    with pytest.raises(PermissionError,match='leak'):scanner.scan_packet(packet,fixture)


def test_privacy_scanner_binds_receipt_to_current_tree(packet,tmp_path):
    import scan_release_credentials as scanner
    fixture=tmp_path/'fixture_vault.py';vault=tmp_path/'fixture.key';vault.write_text('fixture marker')
    fixture.write_text('from pathlib import Path\nSECRETS_PATH=Path('+repr(str(vault))+')\ndef read_secrets():return {"api_key":"fixture-secret-for-scanner"}\n')
    result=scanner.scan_packet(packet,fixture)
    assert result['tree_sha256']==release.tree_hash(packet)


def test_privacy_scanner_can_scan_its_own_shipped_source(packet,tmp_path):
    import scan_release_credentials as scanner
    tools=packet/'Studio/tools';tools.mkdir()
    (tools/'scan_release_credentials.py').write_bytes(Path(scanner.__file__).read_bytes())
    fixture=tmp_path/'fixture_vault.py';vault=tmp_path/'fixture.key';vault.write_text('fixture marker')
    fixture.write_text('from pathlib import Path\nSECRETS_PATH=Path('+repr(str(vault))+')\ndef read_secrets():return {"api_key":"fixture-secret-for-scanner"}\n')
    assert scanner.scan_packet(packet,fixture)['passed']


def test_prepare_generated_metadata_is_not_mistaken_for_immutable_source(tmp_path):
    import io,zipfile
    source=tmp_path/'source';(source/'docs').mkdir(parents=True);(source/'tools/acceptance').mkdir(parents=True)
    (source/'app.py').write_text('pass\n')
    for name in ('SOURCE_PROVENANCE.json','FRESH_PC_RELEASE_GATE.json','CREDENTIAL_SCAN.json'):
        release.write(source/'docs'/name,{'old_source_metadata':True})
    sdk=io.BytesIO()
    with zipfile.ZipFile(sdk,'w'):pass
    baseline=tmp_path/'baseline.zip'
    with zipfile.ZipFile(baseline,'w') as z:
        z.writestr('baseline/Studio/app.py','pass\n');z.writestr('baseline/Studio/tools/sdk-payload.zip',sdk.getvalue())
    proof=tmp_path/'proof';proof.mkdir();identity=release.source_hash(source)
    release.write(proof/'regression-source.json',{'source_sha256':identity})
    release.write(proof/'http-runtime-acceptance.json',{'source_sha256':identity,'paid_calls':0,'official_runtime':{'local_runtime':True}})
    (proof/'full-regression-2.log').write_text('1 passed in 0.1s')
    for name in ('frontend.log','official-sdk-tests.log'):(proof/name).write_text('# pass 1\n# fail 0\n')
    target=tmp_path/'complete-candidate'
    result=release.prepare(source,baseline,release.sha(baseline),target,'R9.1',proof)
    assert not any(name in result['source_hashes'] for name in release.GENERATED)
    approve(target)
    assert release.finalize(target)['fresh_pc_accepted'] is False
