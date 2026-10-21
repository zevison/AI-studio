from __future__ import annotations
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import backup_manager
try:
    result=backup_manager.create_backup(include_outputs=True)
    print('已完成一致性备份：',result['path'])
except Exception as exc:
    print('备份失败，未修改原始数据：',exc)
    raise SystemExit(1)
finally:
    if sys.stdin.isatty():input('按回车键退出…')
