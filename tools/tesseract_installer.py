"""Windows Tesseract OCR installer WITHOUT winget.

The binary is intentionally NOT shipped in the Studio code ZIP. It is fetched
from the version-pinned, upstream-linked GitHub release, checked against a
pinned SHA-256 from the independent Chocolatey verification manifest, and then
started using the interactive Windows GUI (with the user's consent).

No arbitrary URLs or unsigned/unverified binaries are ever executed. Missing
OCR never blocks Studio's core video/image generation functionality.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.tesseract_support import check_tesseract  # noqa: E402

VERSION = '5.5.3.20260724'
FILENAME = f'tesseract-ocr-w64-setup-{VERSION}.exe'
SOURCE = ('https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/' + FILENAME)
SHA256 = 'BEE9E3434BD94FD65387D9BE28CD467A41F61B1275383B55B0F59A1331270AE4'
DOWNLOAD_DIR = ROOT / 'tools' / 'downloads'
DEST = DOWNLOAD_DIR / FILENAME
MIN_BYTES = 12_000_000
MAX_BYTES = 120_000_000
ALLOWED_REDIRECT_HOSTS = {'github.com', 'release-assets.githubusercontent.com',
                          'objects.githubusercontent.com'}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest().upper()


def verify_installer(path):
    path = Path(path)
    if not path.is_file():
        return False
    try:
        size = path.stat().st_size
        if not MIN_BYTES <= size <= MAX_BYTES:
            return False
        with path.open('rb') as f:
            if f.read(2) != b'MZ':
                return False
        return sha256_file(path) == SHA256
    except (OSError, ValueError):
        return False


def download_installer(destination=DEST, *, opener=None):
    """Download only the exact pinned publisher release; never accept arbitrary URLs."""
    destination = Path(destination)
    if verify_installer(destination):
        print('[OK] 本地安装程序 SHA256 校验通过，直接复用。', flush=True)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix('.download')
    try:
        partial.unlink(missing_ok=True)
        req = urllib.request.Request(SOURCE, headers={'User-Agent': 'SeedanceStudio/4.2.7', 'Accept': 'application/octet-stream'})
        opener = opener or urllib.request.urlopen
        with opener(req, timeout=60) as response:
            final_url = response.geturl()
            parsed = urllib.parse.urlsplit(final_url)
            if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_REDIRECT_HOSTS:
                raise RuntimeError('下载跳转到了未经允许的网站，已拒绝执行。')
            size = 0
            with partial.open('wb') as writer:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise RuntimeError('下载文件超过安全大小限制。')
                    writer.write(chunk)
        if not verify_installer(partial):
            raise RuntimeError('安装程序 SHA256 校验不通过，下载文件不会执行。')
        partial.replace(destination)
        return destination
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def install_tesseract(*, consent=True, downloader=None, run_installer=None, verifier=None, platform=None):
    platform = os.name if platform is None else platform
    verifier = verifier or check_tesseract
    existing = verifier()
    if existing['available']:
        print('[OK] Tesseract 已安装：' + str(existing['path']), flush=True)
        return True
    if platform != 'nt':
        print('[INFO] 非 Windows 环境，不安装 Windows Tesseract。', flush=True)
        return False
    if not consent:
        print('[INFO] 未获得安装确认，本次不执行外部安装程序。', flush=True)
        return False
    try:
        print('[INFO] 通过 GitHub 上游发布页下载 Tesseract 5.5.3；无需 winget。', flush=True)
        print('[INFO] 安装前将校验固定 SHA256，安装窗口可能要求 Windows 管理员批准。', flush=True)
        location = (downloader or download_installer)()
        if not verify_installer(location):
            raise RuntimeError('文件校验未通过，拒绝执行安装程序。')
        print('[OK] 安装程序校验通过。即将打开安装向导，请选择默认路径。', flush=True)
        command = [str(location)]
        launcher = run_installer or subprocess.run
        result = launcher(command, timeout=1200, check=False)
        if getattr(result, 'returncode', None) != 0:
            print(f'[WARN] Tesseract 安装器退出码：{result.returncode}。不覆盖原有环境。', flush=True)
        # Windows may register the new binary after the installer process exits.
        for i in range(4):
            state = verifier()
            if state['available']:
                print('[OK] Tesseract 已安装并通过运行验证：' + str(state['path']), flush=True)
                return True
            if i < 3:
                time.sleep(1)
        print('[WARN] 安装结束，但未检测到可运行的 Tesseract。请检查安装向导是否完成。', flush=True)
        return False
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, TimeoutError) as exc:
        print('[WARN] Tesseract 自动准备失败：' + str(exc)[:500], flush=True)
        print('[INFO] 备用下载页：https://github.com/UB-Mannheim/tesseract/wiki', flush=True)
        print('[INFO] 可将官方同名安装文件放到 tools/downloads 后重新执行。', flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='仅检查当前安装是否可用')
    ap.add_argument('--install', action='store_true', help='经用户主动运行，执行 Tesseract 安装')
    args = ap.parse_args()
    result = check_tesseract()
    if args.check or not args.install:
        print('Tesseract OCR:', '可用' if result['available'] else '未安装 / 未通过验证', result.get('path') or '')
        return 0 if result['available'] else 10
    return 0 if install_tesseract() else 10


if __name__ == '__main__':
    raise SystemExit(main())
