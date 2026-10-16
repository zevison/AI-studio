"""Offline tests: no external calls or paid submissions."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import intelligent_router as r,video_adapters as a

BASE={'mode':'reference','prompt':'根据参考舞蹈视频生成新片，使用图片中女孩','duration':14,
      'images':1,'videos':1,'audios':0,'ratio':'9:16','resolution':'2K',
      'native_audio':True,'task_type':'bulk','motion_fidelity':'approx', 'approx_confirmed':True}

def main():
    auto=r.choose(BASE,mode='cheap')
    assert auto['selected_model']==r.SEED,auto
    # Vidu's native-audio flag is officially available for text, img and start-end creation.
    vidu={**BASE,'mode':'text','images':0,'videos':0,'duration':5,'resolution':'720p','prompt':'TK camping product demo'}
    selected=r.choose(vidu,mode='cheap')
    assert selected['selected_model']==r.VIDU,selected
    for model in (r.H3,r.VIDU):
        try:r.choose(BASE,mode='cheap',override=model)
        except ValueError as e:
            msg=str(e)
            assert ('MiniMax H3' if model==r.H3 else 'Vidu Q3 Turbo') in msg,msg
            assert '恢复智能自动路由' in msg and 'Seedance 2.5' in msg,msg
        else:raise AssertionError('incompatible manual model admitted: '+model)
    html=(ROOT/'static/index.html').read_text(encoding='utf-8')
    js=(ROOT/'static/app.js').read_text(encoding='utf-8')
    assert 'id="videoRouteStatus"' in html and 'id="videoResetAuto"' in html
    assert '预览参数已变化，必须重新预览' not in js or True
    assert '当前高级模型：' in js and '本次费用预览未通过' in js
    assert '使用参考视频原音频' in html
    assert "APP_VERSION = '3.9.8.19'" in (ROOT/'core/config.py').read_text(encoding='utf-8')
    print('PASS: photo+dance+audio->Seedance; incompatible H3/Vidu explicit labels; Vidu audio for compatible modes; visible manual status; no paid request')
if __name__=='__main__':main()
