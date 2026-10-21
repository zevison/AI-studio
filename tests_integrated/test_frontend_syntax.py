"""前端脚本语法检查。

一个 JS 语法错误会让整个 app.js 不执行：页面看似正常（HTML 静态内容还在），
但所有导航按钮、状态刷新、弹窗都无响应。因此每次发布前必须确认每个
static/*.js 都能被解析。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / 'static').glob('*.js'))


def _node():
    return shutil.which('node')


@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.name)
def test_frontend_script_parses(script):
    node = _node()
    if not node:
        pytest.skip('本机没有 node，跳过前端语法检查')
    result = subprocess.run([node, '--check', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]


def test_every_static_script_is_covered():
    assert SCRIPTS, 'static 目录下没有找到任何前端脚本'
