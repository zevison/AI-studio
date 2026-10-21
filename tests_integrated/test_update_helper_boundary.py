import hashlib
import pytest
from desktop.update_helper import validate_runner
from core.update_security import UpdateRejected


@pytest.mark.parametrize('files',[[],[{'path':'resource'}],[{'path':[]}],
    [{'path':'SeedanceStudio.exe'},{'path':'SeedanceStudio.exe'}]])
def test_incomplete_or_duplicate_runner_manifest_refused(tmp_path,files):
    identifier='a'*32;runner=tmp_path/'updates/runners'/identifier/'SeedanceStudio.exe'
    runner.parent.mkdir(parents=True);runner.write_bytes(b'fixture')
    plan={'id':identifier,'home':str(tmp_path),'runner':str(runner),'runner_files':files}
    with pytest.raises(UpdateRejected):validate_runner(plan,runner)


def test_runner_tamper_refused_before_execution(tmp_path):
    identifier='a'*32;runner=tmp_path/'updates/runners'/identifier/'SeedanceStudio.exe'
    runner.parent.mkdir(parents=True);runner.write_bytes(b'fixture')
    plan={'id':identifier,'home':str(tmp_path),'runner':str(runner),'runner_files':[
        {'path':'SeedanceStudio.exe','size':7,'sha256':hashlib.sha256(b'fixture').hexdigest()}]}
    validate_runner(plan,runner)
    runner.write_bytes(b'changed')
    with pytest.raises(UpdateRejected,match='不完整'):validate_runner(plan,runner)
