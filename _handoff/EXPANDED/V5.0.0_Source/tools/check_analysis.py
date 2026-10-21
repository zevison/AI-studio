from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import system_health, hypit_engine

h=system_health.report()
print('Seedance Studio V3.9.0 | 本地视频分析能力检查')
print('='*72)
for level,title in [('required','[核心 / 必须]'),('recommended','[增强 / 推荐]')]:
    print('\n'+title)
    for x in h.get('checks',[]):
        if x.get('level')!=level:continue
        mark='OK' if x.get('ok') else 'MISSING'
        print(f"{mark:7} {x.get('label')}: {x.get('detail') or ''}")
print('\n[分析引擎]')
print('Hypit 工作流引擎:',hypit_engine.ENGINE_NAME,hypit_engine.ENGINE_VERSION)
print('媒体探测链：FFprobe → FFmpeg → OpenCV（按需降级）')
print('镜头检测链：PySceneDetect → FFmpeg（按需降级）')
print('语音链：WhisperX → Faster-Whisper（按需降级）')
print('视觉链：OpenCV + MediaPipe + YOLO/ByteTrack（组件可用时启用）')
print('\n[结论]')
if h.get('required_ok'):
    print('✓ 核心本地解析能力可用。')
else:
    print('! 核心能力不完整。请运行 01_INSTALL.cmd，或在“状态与设置 → 系统体检”点击一键修复。')
if h.get('recommended_ok'):
    print('✓ 增强分析能力已全部就绪。')
else:
    print('提示：部分增强能力未安装；工作台会自动降级，不会因此重复调用付费视频创建接口。')
print('\n按回车键退出…')
try: input()
except EOFError: pass
