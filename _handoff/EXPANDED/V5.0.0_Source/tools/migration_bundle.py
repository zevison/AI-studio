from __future__ import annotations
import argparse, json, shutil, sys, tempfile, time, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import backup_manager, config


def choose_zip():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
        p=filedialog.askopenfilename(title='选择 Seedance Studio 迁移备份',filetypes=[('Seedance Studio 备份','*.zip'),('ZIP','*.zip')])
        root.destroy();return Path(p) if p else None
    except Exception:return None


def do_export(include_outputs=False):
    r=backup_manager.create_backup(include_outputs);print('[OK] 已创建迁移包：',r['path']);return 0


def do_import(src:Path|None):
    src=src or choose_zip()
    if not src or not src.exists():print('[INFO] 未选择迁移包。');return 2
    print('[安全] 先备份当前配置/数据库/素材，再导入。')
    current=backup_manager.create_backup(False);print('[OK] 当前环境备份：',current['path'])
    with tempfile.TemporaryDirectory() as td:
        d=Path(td)
        with zipfile.ZipFile(src) as z:
            bad=[n for n in z.namelist() if n.startswith('/') or '..' in Path(n).parts]
            if bad:raise RuntimeError('迁移包包含非法路径，已拒绝导入。')
            z.extractall(d)
        manifest=d/'backup_manifest.json'
        if not manifest.exists():raise RuntimeError('这不是 V3.9.0 迁移备份：缺少 backup_manifest.json')
        allowed=['.env','data/studio.db','data/secrets.json','data/shared_profile.json']
        for rel in allowed:
            srcp=d/rel
            if srcp.exists():
                dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(srcp,dst);print('[OK] 恢复',rel)
        src_assets=d/'data'/'assets'
        if src_assets.exists():
            config.ASSET_DIR.mkdir(parents=True,exist_ok=True)
            for f in src_assets.rglob('*'):
                if f.is_file():
                    rel=f.relative_to(src_assets);dst=config.ASSET_DIR/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dst)
            print('[OK] 素材库已恢复')
        src_out=d/'data'/'outputs'
        if src_out.exists():
            config.OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
            for f in src_out.rglob('*'):
                if f.is_file():
                    rel=f.relative_to(src_out);dst=config.OUTPUT_DIR/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dst)
            print('[OK] 生成结果已恢复')
    print('[完成] 迁移导入完成。请重新启动 Seedance Studio；旧数据库会自动非破坏升级。')
    return 0


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['export','import']);ap.add_argument('--include-outputs',action='store_true');ap.add_argument('--file');a=ap.parse_args()
    return do_export(a.include_outputs) if a.action=='export' else do_import(Path(a.file) if a.file else None)
if __name__=='__main__':raise SystemExit(main())
