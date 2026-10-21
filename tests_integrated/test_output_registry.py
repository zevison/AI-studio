import json
import pytest
from core import output_registry


def test_output_change_keeps_old_urls_without_moving_media(tmp_path):
    home=tmp_path/'用户 数据';old=tmp_path/'旧 成片';new=tmp_path/'新 成片'
    old.mkdir();new.mkdir();(old/'first.mp4').write_bytes(b'first')
    output_registry.register(home,old,home/'data/outputs')
    output_registry.register(home,new,home/'data/outputs')
    assert output_registry.locate(home,new,home/'data/outputs','first.mp4')==old/'first.mp4'
    assert (old/'first.mp4').read_bytes()==b'first'


@pytest.mark.parametrize('name',['../secret','a/b','a\\b','C:secret'])
def test_registry_refuses_path_escape(tmp_path,name):
    with pytest.raises(PermissionError):output_registry.locate(tmp_path,tmp_path,tmp_path,name)


def test_corrupt_history_is_preserved_and_not_overwritten(tmp_path):
    file=tmp_path/'output-roots.json';file.write_bytes(b'{broken')
    with pytest.raises(ValueError):output_registry.register(tmp_path,tmp_path,tmp_path)
    assert file.read_bytes()==b'{broken'
    assert output_registry.roots(tmp_path,tmp_path,tmp_path)==[tmp_path.resolve()]
