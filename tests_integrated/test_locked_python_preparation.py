import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import prepare_locked_python as preparation


def test_corrupt_cached_wheel_is_preserved_and_never_installed(tmp_path):
    target=tmp_path/'fixture.whl';target.write_bytes(b'modified')
    record={'url':'https://files.pythonhosted.org/packages/fixture.whl','filename':target.name,'sha256':'0'*64}
    with pytest.raises(ValueError,match='preserved'):preparation.download(record,tmp_path)
    assert target.read_bytes()==b'modified'


@pytest.mark.parametrize('url',['http://files.pythonhosted.org/package.whl',
    'https://files.pythonhosted.org.evil.example/package.whl','https://127.0.0.1/package.whl'])
def test_untrusted_wheel_origin_refused_before_any_download(tmp_path,url):
    record={'url':url,'filename':'fixture.whl','sha256':'0'*64}
    with pytest.raises(ValueError,match='exact public PyPI'):preparation.download(record,tmp_path)
    assert not list(tmp_path.iterdir())
