from __future__ import annotations
import subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PYEXE=ROOT/'.venv'/'Scripts'/'python.exe'

def main():
    print('='*62)
    print('Seedance Studio V3.9.0｜本地语音时间轴增强')
    print('='*62)
    if not PYEXE.exists():
        print('[错误] 没有找到 .venv，请先运行 01_INSTALL.cmd。')
        return 2
    print('将安装 Faster-Whisper（高速语音识别 + 逐词时间戳）。WhisperX 等增强分析组件统一由 01_INSTALL.cmd 安装。')
    print('它不调用付费 API。首次分析时会下载本地识别模型，需要额外磁盘空间和时间。')
    print('适合：口播、采访、播客、字幕跟随型爆款。纯动作/纯音乐视频可以不装。')
    ans=input('\n输入 Y 继续安装，其他键取消：').strip().lower()
    if ans!='y':
        print('已取消。')
        return 0
    cmd=[str(PYEXE),'-m','pip','install','--disable-pip-version-check','faster-whisper']
    rc=subprocess.run(cmd,cwd=ROOT).returncode
    if rc!=0:
        print('[错误] Faster-Whisper 安装失败。视觉分析仍可使用。')
        return rc or 1
    verify=subprocess.run([str(PYEXE),'-c','import faster_whisper; print("OK")'],cwd=ROOT,capture_output=True,text=True)
    if verify.returncode!=0:
        print('[错误] 安装完成但导入验证失败。')
        return 3
    print('[OK] Faster-Whisper 安装并验证成功。')
    print('请重新运行 07_检查Hypit本地解析能力.cmd。')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
