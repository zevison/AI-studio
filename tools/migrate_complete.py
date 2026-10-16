from pathlib import Path
import sys,shutil,datetime,os
root=Path(__file__).resolve().parents[1];src=Path(sys.argv[1].strip().strip('"')).resolve() if len(sys.argv)>1 else None
if not src or not (src/'app.py').exists() or not (src/'core/config.py').exists():raise SystemExit('旧版路径无效，未修改任何文件')
if src==root:raise SystemExit('源目录与新目录相同，取消操作')
backup=root/'data'/'migration_backups'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
for rel in ('.env','data/secrets.json','data/settings.json','data/studio.db','data/prompt_library.db','data/visual_prompt_library.db'):
  a=src/rel;b=root/rel
  if a.is_file():
    if b.is_file():
      dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(b,dest)
    b.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(a,b);print('已迁移',rel)
for rel in ('data/assets','data/outputs'):
  a=src/rel;b=root/rel
  if a.is_dir():
    b.mkdir(parents=True,exist_ok=True)
    for file in a.rglob('*'):
      if file.is_file() and not file.is_symlink():
        target=b/file.relative_to(a)
        if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
    print('已合并',rel)
print('迁移完成。新版本代码未被旧版覆盖。')
