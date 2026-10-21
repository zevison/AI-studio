from __future__ import annotations
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding='utf-8-sig', errors='ignore').splitlines():
        s = raw.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        out[k.strip()] = v
    return out


def merge_env(old_env: Path, template: Path, dst: Path):
    old = parse_env(old_env)
    lines = template.read_text(encoding='utf-8-sig', errors='ignore').splitlines()
    merged = []
    seen = set()
    for line in lines:
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, _ = s.split('=', 1)
            k = k.strip(); seen.add(k)
            if k in old:
                line = f'{k}={old[k]}'
        merged.append(line)
    # Preserve unknown non-secret compatibility keys from older builds at the end.
    for k, v in old.items():
        if k not in seen and not k.startswith('R2_'):
            merged.append(f'{k}={v}')
    dst.write_text('\n'.join(merged).rstrip() + '\n', encoding='utf-8')


print('Seedance Studio V3.9.0｜迁移旧版配置和历史')
old_raw = input('请输入旧版 Seedance Studio 文件夹完整路径：').strip().strip('"')
old = Path(old_raw)
if not old.exists():
    print('[错误] 路径不存在。'); input('按回车键退出…'); raise SystemExit(2)

old_env = old / '.env'
template = ROOT / '.env.example'
if old_env.exists() and template.exists():
    merge_env(old_env, template, ROOT / '.env')
    print('[OK] 已迁移旧版 API / 模型 / 接口配置，并保留 V3.4.1 OSS / 任务持久化配置字段。')
elif old_env.exists():
    shutil.copy2(old_env, ROOT / '.env'); print('[OK] 已复制旧版 .env。')
else:
    print('[提示] 旧版没有 .env，保留新版当前配置。')

if (old / 'data').exists():
    shutil.copytree(old / 'data', ROOT / 'data', dirs_exist_ok=True)
    print('[OK] 已合并旧版 data（数据库、素材、输出、历史）。')
else:
    print('[提示] 旧版没有 data 文件夹。')

print('[说明] 不迁移旧版 .venv，避免把另一台电脑/Windows 用户的 Python 路径带入新版。')
print('[说明] OSS 配置会自动迁移到 Windows 本机共享配置；完成一次后，后续版本无需重复填写。')
print('迁移完成。旧版文件不会被删除。')
input('按回车键退出…')
