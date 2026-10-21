import os
import pytest


@pytest.mark.parametrize('replace_owner',[False,True])
def test_startup_database_failure_releases_bound_server_and_owned_pid(tmp_path,monkeypatch,replace_owner):
    import app
    marker=tmp_path/'backend.pid'
    closed=[]
    class Server:
        server_address=('127.0.0.1',56789)
        def __init__(self,*args): pass
        def server_close(self): closed.append(True)
        def serve_forever(self): pytest.fail('failed initialization must not serve')
    def recover():
        assert marker.read_text()==str(os.getpid())
        if replace_owner: marker.write_text('another-owner')
        raise RuntimeError('injected database recovery failure')
    monkeypatch.setattr(app,'ThreadingHTTPServer',Server)
    monkeypatch.setattr(app.config,'PORT',0)
    monkeypatch.setattr(app.config,'PID_PATH',marker)
    monkeypatch.setattr(app.team_safety,'recover',recover)
    monkeypatch.setattr(app.hypit_service,'close_studio',lambda:None)
    monkeypatch.setattr(app.tunnel,'stop',lambda:None)
    with pytest.raises(RuntimeError,match='injected database'): app.run()
    assert closed==[True]
    assert marker.exists()==replace_owner
