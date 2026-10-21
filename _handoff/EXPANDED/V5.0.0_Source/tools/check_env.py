from __future__ import annotations
import os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
print('Seedance Studio V3.9.8.9｜环境检查')
print('='*58)
print('Python:', sys.version.split()[0], sys.executable)
try:
    from core.us_marketing import context
    print('美国时区:', '正常 (' + context()['as_of_us'] + ')')
except Exception as exc:
    print('美国时区: 异常 -', exc)
venv=ROOT/'.venv'/'Scripts'/'python.exe'
print('.venv:', '已建立' if venv.exists() else '未建立')
env=ROOT/'.env'
print('.env:', '存在' if env.exists() else '缺失')
key=''
if env.exists():
    for line in env.read_text(encoding='utf-8-sig',errors='ignore').splitlines():
        if line.strip().startswith('OPENLUX_API_KEY='):
            key=line.split('=',1)[1].strip();break
print('OPENLUX_API_KEY:', '已配置' if len(key)>10 and '你的' not in key else '未配置')
cf=ROOT/'tools'/'cloudflared.exe'
print('cloudflared.exe:', '已安装' if cf.exists() else '未安装')
print('\n提示：完整解析能力可运行 07_检查Hypit本地解析能力.cmd；缺失组件可运行 01_INSTALL.cmd。')
input('\n按回车键退出…')
