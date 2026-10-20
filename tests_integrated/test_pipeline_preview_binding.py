import pytest
from core import hot_radar


def test_quote_identity_changes_with_product_source_model_and_price():
    base=['model','system','verified product and video sha',2048,0.01]
    identity=hot_radar._script_preview_hash(*base)
    for index,value in enumerate(['other-model','other-instructions','other-product-or-video',4096,0.02]):
        changed=base.copy();changed[index]=value
        assert hot_radar._script_preview_hash(*changed)!=identity


@pytest.mark.parametrize('client_hash',[None,'outdated'])
def test_stale_preview_rejected_before_credentials_or_paid_work(monkeypatch,client_hash):
    monkeypatch.setattr(hot_radar,'script_preview',lambda _: {'preview_hash':'current'})
    def forbidden(*args):raise AssertionError('Must stop before credentials/model calls')
    monkeypatch.setattr(hot_radar.secrets_store,'get',forbidden)
    with pytest.raises(ValueError,match='重新预检'):
        hot_radar.generate_script({'approve_paid':True,'preview_hash':client_hash})
