from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Permit importing the shared, read-only executable resolver in core/.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / "tools" / "runtime"
BIN = RUNTIME / "bin"
CACHE = ROOT / "tools" / "cache"
DATA = ROOT / "data"
for p in (RUNTIME, BIN, CACHE, DATA):
    p.mkdir(parents=True, exist_ok=True)

REPORT_JSON = DATA / "production_tools_install_report.json"
REPORT_TXT = DATA / "production_tools_install_report.txt"

FFMPEG_SOURCES = [
    ("Gyan FFmpeg Essentials", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("BtbN FFmpeg Build", "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"),
]

RESULTS: list[dict] = []


WHISPERX_VENV = RUNTIME / "whisperx_py313"

def whisperx_sidecar_exe() -> Path:
    if os.name == "nt":
        return WHISPERX_VENV / "Scripts" / "whisperx.exe"
    return WHISPERX_VENV / "bin" / "whisperx"

def whisperx_sidecar_python() -> Path:
    if os.name == "nt":
        return WHISPERX_VENV / "Scripts" / "python.exe"
    return WHISPERX_VENV / "bin" / "python"

def _capture(cmd: list[str], timeout: int = 30) -> str:
    try:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
        return (cp.stdout or '').strip()
    except Exception:
        return ''

def find_python313() -> str | None:
    """Find a Python 3.13 interpreter without changing the Studio main runtime."""
    candidates: list[Path] = []
    if os.name == 'nt':
        # Prefer the Python launcher because it is independent from PATH order.
        if shutil.which('py'):
            out = _capture(['py', '-3.13', '-c', 'import sys;print(sys.executable)'])
            if out:
                candidates.append(Path(out.splitlines()[-1].strip()))
        for n in ('python3.13', 'python313'):
            q = shutil.which(n)
            if q:
                candidates.append(Path(q))
        local = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Python' / 'Python313' / 'python.exe'
        candidates += [local, Path(r'C:\Python313\python.exe')]
    else:
        for n in ('python3.13',):
            q = shutil.which(n)
            if q:
                candidates.append(Path(q))
    for c in candidates:
        try:
            if c.exists():
                ver = _capture([str(c), '-c', 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")'])
                if ver == '3.13':
                    return str(c)
        except Exception:
            pass
    return None

def ensure_python313() -> str | None:
    py = find_python313()
    if py:
        return py
    if os.name == 'nt' and shutil.which('winget'):
        log('\nWhisperX 与当前 Python 3.14 不兼容；将单独安装 Python 3.13 运行时。')
        code = run_visible([
            'winget','install','--id','Python.Python.3.13','-e','--scope','user',
            '--accept-package-agreements','--accept-source-agreements'
        ], timeout=1200)
        if code == 0:
            time.sleep(2)
            py = find_python313()
            if py:
                return py
    return None

def install_whisperx_compatible() -> bool:
    """Install WhisperX in an isolated Python 3.13 sidecar when Studio uses Python 3.14+.

    This avoids downgrading or destabilizing the main Studio .venv.
    """
    # Existing global/project executable is always accepted.
    existing = find_exe('whisperx')
    sidecar = whisperx_sidecar_exe()
    if sidecar.exists():
        record('WhisperX（精确语音时间轴）', 'skip', f'独立 Python 3.13 环境已存在：{sidecar}')
        return True
    if existing:
        record('WhisperX（精确语音时间轴）', 'skip', f'已存在：{existing}')
        return True

    if sys.version_info < (3, 14):
        return pip_install_if_missing('WhisperX（精确语音时间轴）', 'whisperx', 'whisperx')

    log('\n' + '-' * 66)
    log('安装 WhisperX（精确语音时间轴）...')
    log(f'当前 Studio Python {sys.version_info.major}.{sys.version_info.minor}，WhisperX 依赖 ctranslate2 暂不支持 Python 3.14。')
    log('将使用独立 Python 3.13 sidecar（旁路运行环境），不会修改主程序 Python。')

    py313 = ensure_python313()
    if not py313:
        record('WhisperX（精确语音时间轴）', 'warn', '未能获取 Python 3.13；已自动使用 Faster-Whisper 逐词时间轴备用能力')
        return False

    try:
        if WHISPERX_VENV.exists() and not whisperx_sidecar_python().exists():
            shutil.rmtree(WHISPERX_VENV, ignore_errors=True)
        if not whisperx_sidecar_python().exists():
            code = run_visible([py313, '-m', 'venv', str(WHISPERX_VENV)], timeout=300)
            if code != 0:
                record('WhisperX（精确语音时间轴）', 'warn', f'创建 Python 3.13 独立环境未完成（退出码 {code}）；已保留 Faster-Whisper 备用能力')
                return False

        side_py = str(whisperx_sidecar_python())
        run_visible([side_py, '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip', 'setuptools', 'wheel'], timeout=600)
        code = run_visible([side_py, '-m', 'pip', 'install', '--disable-pip-version-check', '--prefer-binary', 'whisperx'], timeout=1800)
        if code == 0 and sidecar.exists():
            record('WhisperX（精确语音时间轴）', 'ok', f'已安装到独立 Python 3.13 环境：{sidecar}')
            return True
        record('WhisperX（精确语音时间轴）', 'warn', f'独立 Python 3.13 环境安装未完成（退出码 {code}）；已自动使用 Faster-Whisper 备用链路')
        return False
    except Exception as e:
        record('WhisperX（精确语音时间轴）', 'warn', f'独立环境安装未完成：{type(e).__name__}: {e}；已自动使用 Faster-Whisper 备用链路')
        return False


def log(msg=""):
    print(msg, flush=True)


def record(name: str, status: str, detail: str = ""):
    RESULTS.append({"name": name, "status": status, "detail": detail})
    mark = {"ok": "[OK]", "skip": "[SKIP]", "fail": "[FAIL]", "warn": "[WARN]"}.get(status, "[INFO]")
    log(f"{mark} {name}: {detail}")


def module_exists(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def local_exe(name: str) -> Path:
    return BIN / (name + ".exe" if os.name == "nt" and not name.lower().endswith(".exe") else name)


def find_exe(name: str) -> str | None:
    p = local_exe(name)
    if p.exists():
        return str(p)
    q = shutil.which(name)
    if q:
        return q
    # Common Windows locations that may not be visible in the current process PATH yet.
    if os.name == "nt":
        candidates = []
        if name.lower() == "tesseract":
            candidates += [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
            ]
        for c in candidates:
            if c.exists():
                return str(c)
    return None


def run_visible(cmd: list[str], timeout: int | None = None) -> int:
    log("\n> " + " ".join(map(str, cmd)))
    try:
        p = subprocess.Popen(cmd)
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f"[WARN] 命令运行超过 {timeout} 秒，已终止并继续安装其他组件。")
            p.kill()
            try:
                p.wait(timeout=10)
            except Exception:
                pass
            return 124
    except Exception as e:
        log(f"[FAIL] 无法启动命令：{type(e).__name__}: {e}")
        return 1


def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f} {u}"
        v /= 1024
    return f"{v:.1f} GB"


def download_with_progress(url: str, dest: Path, label: str, connect_timeout: int = 25) -> Path:
    """Chunked resumable download with visible progress, speed and ETA.

    A .part file is kept when the connection fails so rerunning the installer can resume.
    If a server ignores Range, the partial file is safely restarted.
    """
    part = dest.with_suffix(dest.suffix + ".part")
    resume_from = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "SeedanceStudio/3.9.3"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
        log(f"发现未完成下载，将尝试从 {human_bytes(resume_from)} 继续。")

    req = urllib.request.Request(url, headers=headers)
    started = time.time()
    last_print = 0.0
    try:
        with urllib.request.urlopen(req, timeout=connect_timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            content_len = int(resp.headers.get("Content-Length") or 0)
            if resume_from and code != 206:
                # Server did not honor Range; restart cleanly.
                resume_from = 0
                try:
                    part.unlink(missing_ok=True)
                except Exception:
                    pass
            total = resume_from + content_len if content_len else 0
            mode = "ab" if resume_from and code == 206 else "wb"
            downloaded = resume_from
            with open(part, mode) as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_print >= 0.7:
                        elapsed = max(now - started, 0.01)
                        session_bytes = max(downloaded - resume_from, 0)
                        speed = session_bytes / elapsed
                        if total:
                            pct = min(100.0, downloaded * 100.0 / total)
                            remain = max(total - downloaded, 0)
                            eta = remain / speed if speed > 1 else 0
                            log(
                                f"\r{label}: {pct:5.1f}% | {human_bytes(downloaded)} / {human_bytes(total)} "
                                f"| {human_bytes(speed)}/s | 剩余约 {eta:,.0f} 秒"
                            )
                        else:
                            log(f"\r{label}: 已下载 {human_bytes(downloaded)} | {human_bytes(speed)}/s")
                        last_print = now
        os.replace(part, dest)
        log(f"{label}: 100% | 下载完成 -> {dest.name}")
        return dest
    except Exception:
        # Preserve .part for next run.
        raise


def extract_ffmpeg(zip_path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            ffmpeg_name = next((n for n in names if n.lower().endswith("/bin/ffmpeg.exe")), None)
            ffprobe_name = next((n for n in names if n.lower().endswith("/bin/ffprobe.exe")), None)
            if not ffmpeg_name or not ffprobe_name:
                return False, "压缩包中没有同时找到 ffmpeg.exe 和 ffprobe.exe"
            for src_name, dst_name in ((ffmpeg_name, "ffmpeg.exe"), (ffprobe_name, "ffprobe.exe")):
                with z.open(src_name) as src, open(BIN / dst_name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return True, str(BIN)
    except Exception as e:
        return False, f"解压失败：{type(e).__name__}: {e}"


def ffmpeg_ready() -> bool:
    return bool(find_exe("ffmpeg") and find_exe("ffprobe"))


def install_ffmpeg_windows() -> bool:
    if os.name != "nt":
        if ffmpeg_ready():
            record("FFmpeg + FFprobe", "skip", "系统已存在")
            return True
        record("FFmpeg + FFprobe", "warn", "非 Windows 环境，本安装器不自动下载 Windows 运行时")
        return False

    if ffmpeg_ready():
        record("FFmpeg + FFprobe", "skip", f"已存在：{find_exe('ffprobe')}")
        return True

    # If an external complete install exists beside ffmpeg, reuse it without redownloading.
    system_ff = shutil.which("ffmpeg")
    if system_ff:
        sibling = Path(system_ff).with_name("ffprobe.exe")
        if sibling.exists():
            record("FFmpeg + FFprobe", "skip", f"检测到完整系统安装：{sibling.parent}")
            return True

    cache_zip = CACHE / "ffmpeg-release.zip"
    if cache_zip.exists():
        log(f"发现 FFmpeg 下载缓存：{cache_zip}，先尝试直接使用。")
        ok, detail = extract_ffmpeg(cache_zip)
        if ok and ffmpeg_ready():
            record("FFmpeg + FFprobe", "ok", "使用已有缓存完成安装，无需重新下载")
            return True
        log(f"缓存无效：{detail}，将重新下载。")
        try:
            cache_zip.unlink(missing_ok=True)
        except Exception:
            pass

    errors = []
    for idx, (source_name, url) in enumerate(FFMPEG_SOURCES, 1):
        log("\n" + "=" * 66)
        log(f"FFmpeg/FFprobe 下载源 {idx}/{len(FFMPEG_SOURCES)}：{source_name}")
        log("若网络较慢，窗口会持续显示百分比、速度和预计剩余时间；不是卡死。")
        try:
            download_with_progress(url, cache_zip, "FFmpeg + FFprobe 下载")
            ok, detail = extract_ffmpeg(cache_zip)
            if ok and ffmpeg_ready():
                record("FFmpeg + FFprobe", "ok", f"安装完成：{BIN}")
                return True
            errors.append(f"{source_name}: {detail}")
        except Exception as e:
            msg = f"{source_name}: {type(e).__name__}: {e}"
            errors.append(msg)
            log(f"[WARN] 当前下载源失败：{msg}")
            log("自动切换下一个备用源，不会终止整个安装流程。")
            continue

    # Final fallback: winget. It displays its own progress UI.
    if shutil.which("winget"):
        log("\n直接下载源均失败，尝试 Windows winget 备用安装 FFmpeg Essentials...")
        code = run_visible([
            "winget", "install", "--id", "Gyan.FFmpeg.Essentials", "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ], timeout=900)
        if code == 0:
            record("FFmpeg + FFprobe", "ok", "winget 安装已完成；如当前窗口仍检测不到，请关闭后重新运行体检")
            return True
        errors.append(f"winget exit={code}")

    record("FFmpeg + FFprobe", "fail", "；".join(errors)[-1000:] or "所有安装方式均失败")
    return False


def pip_install_if_missing(display: str, module: str, package: str, timeout: int = 1200) -> bool:
    if module_exists(module):
        record(display, "skip", "已安装，不重复下载")
        return True
    log("\n" + "-" * 66)
    log(f"安装 {display} ...")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "--prefer-binary", package,
    ]
    code = run_visible(cmd, timeout=timeout)
    # New subprocess installed into current interpreter environment; invalidate import cache.
    importlib.invalidate_caches()
    if code == 0 and module_exists(module):
        record(display, "ok", "安装完成")
        return True
    record(display, "fail", f"安装失败或当前 Python 无兼容包（退出码 {code}），已继续后续组件")
    return False


YOLO11N_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt"


def ensure_yolo_model() -> bool:
    model_dir = RUNTIME / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    dst = model_dir / "yolo11n.pt"
    if dst.exists() and dst.stat().st_size > 1024 * 1024:
        record("YOLO11n 本地模型", "skip", f"已存在：{dst}")
        return True
    if not module_exists("ultralytics"):
        record("YOLO11n 本地模型", "warn", "Ultralytics 未安装，已跳过模型准备")
        return False
    try:
        log("\n准备 YOLO11n 本地权重（约 5.4 MB），下载完成后目标追踪不会在分析时静默联网。")
        download_with_progress(YOLO11N_URL, dst, "YOLO11n")
        if dst.exists() and dst.stat().st_size > 1024 * 1024:
            record("YOLO11n 本地模型", "ok", f"已保存到：{dst}")
            return True
        record("YOLO11n 本地模型", "fail", "下载完成但文件大小异常")
        return False
    except Exception as e:
        record("YOLO11n 本地模型", "warn", f"自动下载失败：{type(e).__name__}: {e}；不影响其他本地分析能力")
        return False


def install_tesseract() -> bool:
    """Shared verified installer. No winget dependency or silent fallback."""
    from tesseract_installer import install_tesseract as install_verified
    from core.tesseract_support import check_tesseract
    previous = check_tesseract()
    if previous['available']:
        record('Tesseract OCR', 'skip', f"已存在：{previous['path']}")
        return True
    result = install_verified()
    state = check_tesseract()
    if result and state['available']:
        record('Tesseract OCR', 'ok', f"安装完成、运行验证通过：{state['path']}")
        return True
    record('Tesseract OCR', 'warn', '官方安装源失败或用户未完成向导；已保留其他视频增强组件。请在工作台关闭后重新运行 01_INSTALL.cmd')
    return False


def write_report():
    payload = {
        "version": "3.9.3",
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "results": RESULTS,
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "Seedance Studio V3.9.3 本地工具安装报告",
        "=" * 66,
        f"完成时间：{payload['finished_at']}",
        f"Python：{sys.version.split()[0]}",
        "",
    ]
    for x in RESULTS:
        lines.append(f"[{x['status'].upper():4}] {x['name']}: {x['detail']}")
    REPORT_TXT.write_text("\n".join(lines), encoding="utf-8")


def print_summary():
    log("\n" + "=" * 66)
    log("安装结果汇总")
    log("=" * 66)
    for x in RESULTS:
        icon = {"ok": "√", "skip": "√", "fail": "×", "warn": "!"}.get(x["status"], "-")
        log(f" {icon} {x['name']:<24} {x['detail']}")
    failed = [x for x in RESULTS if x["status"] == "fail"]
    log("\n说明：安装器默认只补缺失组件；重新运行不会重复安装已经存在的工具。")
    if failed:
        log("存在未安装成功的增强组件。主程序仍可启动；可稍后重新运行本安装器继续修复。")
    else:
        log("本地生产工具安装完成。建议下一步运行 11_系统体检.cmd。")
    log(f"详细报告：{REPORT_TXT}")


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--no-pause", action="store_true", help="完成后不等待 Enter")
    args = parser.parse_args()

    # Production tools must be installed into this Studio's own virtual environment.
    # Installing into a random system Python makes a later Studio restart appear as if
    # the tools were never installed. The CMD launcher already enforces this path; this
    # guard also protects users who run the Python file manually.
    expected_venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt":
        if not expected_venv_py.exists():
            log("[FAIL] 未找到 Seedance Studio 本地虚拟环境 .venv。请先运行 01_INSTALL.cmd。")
            return 3
        try:
            if Path(sys.executable).resolve() != expected_venv_py.resolve():
                log("[FAIL] 当前不是使用本项目 .venv 执行安装。请双击 01_INSTALL.cmd。")
                return 4
        except Exception:
            pass

    log("Seedance Studio V3.9.7 本地生产工具安装器")
    log("本版：新增 WhisperX Python 3.13 独立运行环境；主程序 Python 3.14 无需降级。")
    log("=" * 66)

    try:
        install_ffmpeg_windows()

        # Install separately: one package failure must never cancel the rest.
        pip_install_if_missing("OpenCV（计算机视觉）", "cv2", "opencv-python-headless>=4.10")
        pip_install_if_missing("PySceneDetect（镜头检测）", "scenedetect", "scenedetect>=0.6.4")
        pip_install_if_missing("Faster-Whisper（高速语音识别）", "faster_whisper", "faster-whisper>=1.1.0")
        pip_install_if_missing("YOLO + ByteTrack（目标检测与追踪）", "ultralytics", "ultralytics>=8.3")
        ensure_yolo_model()
        # MediaPipe may replace an in-use cv2.pyd through its OpenCV dependency.
        # Reuse the safe installer shared by the Settings / Doctor workflow.
        from install_analysis_stack import studio_is_running, install_mediapipe_safely
        if studio_is_running():
            record("MediaPipe（人体/手部姿态）", "warn", "工作台正在运行，延迟二进制组件安装")
        elif install_mediapipe_safely():
            record("MediaPipe（人体/手部姿态）", "ok", "安全安装/验证完成，保留原 OpenCV")
        else:
            record("MediaPipe（人体/手部姿态）", "warn", "可选增强暂不可用，保留原 OpenCV")
        install_whisperx_compatible()
        install_tesseract()
    except KeyboardInterrupt:
        record("安装流程", "warn", "用户主动中断；已安装完成的组件会保留，下次可继续")
    except Exception as e:
        record("安装流程", "fail", f"未预期错误：{type(e).__name__}: {e}")
    finally:
        write_report()
        print_summary()
        if not args.no_pause:
            try:
                input("\n按 Enter 退出...")
            except EOFError:
                pass


if __name__ == "__main__":
    raise SystemExit(main() or 0)
