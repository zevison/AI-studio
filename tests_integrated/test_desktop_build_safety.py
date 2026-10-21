"""Distribution certificate allowlist is exact and never admits secret PEMs."""
import importlib.util
from pathlib import Path
import shutil
import sys
import certifi
import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import build_desktop_exe as builder


def test_certifi_exact_public_ca_store_allowed(tmp_path):
    import importlib.metadata
    if importlib.metadata.version('certifi')!='2026.7.22':
        pytest.skip('Build requires pinned certifi 2026.7.22 RECORD')
    target=tmp_path/'_internal/certifi/cacert.pem';target.parent.mkdir(parents=True)
    shutil.copy2(certifi.where(),target)
    audit=builder.audit_payload(tmp_path)
    assert audit['findings']==[]
    assert len(audit['verified_public_resource_exemptions'])==1


def test_modified_ca_store_and_arbitrary_pem_stay_blocked(tmp_path):
    target=tmp_path/'_internal/certifi/cacert.pem';target.parent.mkdir(parents=True)
    target.write_bytes(Path(certifi.where()).read_bytes()+b'\nMODIFIED\n')
    (tmp_path/'unrelated.pem').write_text('not a root certificate',encoding='utf8')
    audit=builder.audit_payload(tmp_path)
    assert {r['path'] for r in audit['findings']}=={'_internal/certifi/cacert.pem','unrelated.pem'}
    assert audit['verified_public_resource_exemptions']==[]


def test_build_refuses_unreviewed_transitive_environment(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    target=tmp_path/'requirements';target.mkdir()
    record={'complete':True,'python':sys.version.split()[0],'packages':[{'name':'approved','version':'1.0'}]}
    (target/'python-win-x64-py314-r39-candidate.json').write_text(json.dumps(record),encoding='utf8')
    monkeypatch.setattr(builder,'ROOT',tmp_path)
    monkeypatch.setattr(builder.importlib.metadata,'distributions',lambda:[
        SimpleNamespace(metadata={'Name':'approved'},version='2.0')])
    with pytest.raises(RuntimeError,match='transitive lock'):builder.verify_python_lock()


@pytest.mark.parametrize('mutation',['added','removed','changed'])
def test_payload_mutation_after_capture_blocks_publication(tmp_path,mutation):
    payload=tmp_path/'payload';payload.mkdir()
    original=payload/'runtime.dll';original.write_bytes(b'original')
    before=builder.payload_inventory(payload)
    report=tmp_path/'blocked.json'
    assert builder.verify_payload_unchanged(payload,before,report)==before
    if mutation=='added':(payload/'late.txt').write_text('late')
    elif mutation=='removed':original.unlink()
    else:original.write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='not a deliverable'):
        builder.verify_payload_unchanged(payload,before,report)
    import json
    assert json.loads(report.read_text())[mutation]


def test_top_level_user_state_blocked_but_dependency_data_allowed(tmp_path):
    dependency=tmp_path/'_internal/dependency/data';dependency.mkdir(parents=True)
    (dependency/'resource.txt').write_text('public resource')
    assert builder.audit_payload(tmp_path)['findings']==[]
    state=tmp_path/'_internal/data';state.mkdir()
    (state/'startup_stage.txt').write_text('startup')
    assert {'path':'_internal/data','kind':'application_user_state'} in builder.audit_payload(tmp_path)['findings']
