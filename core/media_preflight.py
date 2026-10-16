from __future__ import annotations

import hashlib
import math
import mimetypes
import os
import subprocess
from pathlib import Path

from . import config, media

MIN_RATIO = 0.4
MAX_RATIO = 2.5
MIN_SIDE = 300
MAX_SIDE = 6000
VIDEO_MIN_PIXELS = 407696
VIDEO_MAX_PIXELS = 8295044
VIDEO_MIN_FPS = 24.0
VIDEO_MAX_FPS = 60.0

NORMALIZED_PREFIX = "_seedance_ready_"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_even(n: float) -> int:
    v = max(2, int(round(n)))
    return v if v % 2 == 0 else v + 1


def _ratio(w, h):
    try:
        return float(w) / float(h)
    except Exception:
        return 0.0


def _image_target(w: int, h: int) -> tuple[int, int, list[str]]:
    """Return a content-preserving canvas that satisfies Seedance image limits."""
    reasons = []
    w = max(1, int(w))
    h = max(1, int(h))

    # First keep original aspect while bringing side lengths into [300, 6000].
    scale_up = max(MIN_SIDE / w, MIN_SIDE / h, 1.0)
    scale_down = min(MAX_SIDE / w, MAX_SIDE / h, 1.0)
    scale = scale_up if scale_up > 1 else scale_down
    if scale != 1.0:
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        reasons.append(f"尺寸 {w}×{h} 自动缩放为 {nw}×{nh}")
        w, h = nw, nh

    r = _ratio(w, h)
    cw, ch = w, h
    if r > MAX_RATIO:
        ch = int(math.ceil(w / MAX_RATIO))
        reasons.append(f"宽高比 {r:.3f} 超过 {MAX_RATIO}，自动补高到 {w}×{ch}")
    elif r < MIN_RATIO:
        cw = int(math.ceil(h * MIN_RATIO))
        reasons.append(f"宽高比 {r:.3f} 低于 {MIN_RATIO}，自动补宽到 {cw}×{h}")

    # Canvas itself must also fit side-length limits.
    if max(cw, ch) > MAX_SIDE:
        s = MAX_SIDE / max(cw, ch)
        cw = max(MIN_SIDE, int(round(cw * s)))
        ch = max(MIN_SIDE, int(round(ch * s)))
        reasons.append(f"画布过大，自动缩放到 {cw}×{ch}")

    cw = max(MIN_SIDE, min(MAX_SIDE, cw))
    ch = max(MIN_SIDE, min(MAX_SIDE, ch))
    return cw, ch, reasons


def inspect_asset(asset: dict) -> dict:
    a = dict(asset or {})
    kind = a.get("kind")
    w = int(a.get("width") or 0)
    h = int(a.get("height") or 0)
    fps = float(a.get("fps") or 0)
    path = Path(a.get("path") or "")

    if (not w or not h) and path.is_file():
        if kind == "image":
            p = media.image_probe(path)
        elif kind == "video":
            p = media.probe(path)
        else:
            p = {}
        w = int(p.get("width") or w or 0)
        h = int(p.get("height") or h or 0)
        fps = float(p.get("fps") or fps or 0)

    out = {
        "asset_id": a.get("id"),
        "name": a.get("original_name") or a.get("stored_name"),
        "kind": kind,
        "width": w or None,
        "height": h or None,
        "fps": fps or None,
        "ratio": (_ratio(w, h) if w and h else None),
        "needs_fix": False,
        "reasons": [],
    }

    if kind == "image":
        if not w or not h:
            out["needs_fix"] = True
            out["reasons"].append("无法读取图片尺寸")
            return out
        if w < MIN_SIDE or h < MIN_SIDE:
            out["needs_fix"] = True
            out["reasons"].append(f"图片边长必须 ≥ {MIN_SIDE}px")
        if w > MAX_SIDE or h > MAX_SIDE:
            out["needs_fix"] = True
            out["reasons"].append(f"图片边长必须 ≤ {MAX_SIDE}px")
        r = _ratio(w, h)
        if r < MIN_RATIO or r > MAX_RATIO:
            out["needs_fix"] = True
            out["reasons"].append(f"图片宽高比 {r:.3f} 不在 {MIN_RATIO}～{MAX_RATIO}")

    elif kind == "video":
        if not w or not h:
            out["needs_fix"] = True
            out["reasons"].append("无法读取视频尺寸")
            return out
        r = _ratio(w, h)
        pixels = w * h
        if r < MIN_RATIO or r > MAX_RATIO:
            out["needs_fix"] = True
            out["reasons"].append(f"视频宽高比 {r:.3f} 不在 {MIN_RATIO}～{MAX_RATIO}")
        if w < MIN_SIDE or h < MIN_SIDE or w > MAX_SIDE or h > MAX_SIDE:
            out["needs_fix"] = True
            out["reasons"].append(f"视频边长需在 {MIN_SIDE}～{MAX_SIDE}px")
        if pixels < VIDEO_MIN_PIXELS or pixels > VIDEO_MAX_PIXELS:
            out["needs_fix"] = True
            out["reasons"].append(f"视频总像素 {pixels:,} 不在允许范围")
        if fps and (fps < VIDEO_MIN_FPS or fps > VIDEO_MAX_FPS):
            out["needs_fix"] = True
            out["reasons"].append(f"视频帧率 {fps:.2f}fps 不在 {VIDEO_MIN_FPS:g}～{VIDEO_MAX_FPS:g}fps")
    return out


def preview_specs(specs: list[dict]) -> dict:
    """Zero-cost preview of fixes. Does not change files."""
    items = []
    warnings = []
    for idx, spec in enumerate(specs or [], start=1):
        a = spec.get("asset") or {}
        info = inspect_asset(a)
        info["index"] = idx
        info["role"] = spec.get("role")
        items.append(info)

        if a.get("kind") == "image" and info.get("needs_fix") and info.get("width") and info.get("height"):
            tw, th, reasons = _image_target(info["width"], info["height"])
            warnings.append(
                f"第{idx}张/个参考素材「{info.get('name') or '未命名'}」将自动适配："
                f"{info['width']}×{info['height']} → {tw}×{th}；"
                "保留完整主体，不需要你修改 API 参数。"
            )
        elif info.get("needs_fix"):
            warnings.append(
                f"第{idx}个参考素材「{info.get('name') or '未命名'}」将在提交前自动预处理："
                + "；".join(info.get("reasons") or [])
            )
    return {"items": items, "warnings": warnings}


def _normalized_name(asset: dict, target_w: int, target_h: int, ext: str) -> str:
    sha = str(asset.get("sha256") or "")
    token = sha[:16] if sha else hashlib.sha256(
        str(asset.get("path") or asset.get("original_name") or "").encode("utf-8")
    ).hexdigest()[:16]
    return f"{NORMALIZED_PREFIX}{token}_{target_w}x{target_h}{ext}"


def normalize_image(asset: dict) -> tuple[dict, dict]:
    from PIL import Image, ImageFilter, ImageOps

    a = dict(asset or {})
    path = Path(a.get("path") or "")
    if not path.is_file():
        raise RuntimeError(f"参考图片本地文件不存在：{path}")

    with Image.open(path) as im0:
        im = ImageOps.exif_transpose(im0).convert("RGB")
        ow, oh = im.size
        tw, th, reasons = _image_target(ow, oh)

        # If already legal, keep original bytes and URL identity.
        legal = (
            MIN_SIDE <= ow <= MAX_SIDE
            and MIN_SIDE <= oh <= MAX_SIDE
            and MIN_RATIO <= _ratio(ow, oh) <= MAX_RATIO
        )
        if legal:
            return a, {
                "changed": False,
                "kind": "image",
                "original": [ow, oh],
                "prepared": [ow, oh],
                "reason": "原图已符合 Seedance 2.5 素材限制",
            }

        # Scale source only when required by side limits; otherwise preserve pixels.
        sw, sh = ow, oh
        scale = min(tw / ow, th / oh)
        if ow < MIN_SIDE or oh < MIN_SIDE or ow > MAX_SIDE or oh > MAX_SIDE:
            sw = max(1, int(round(ow * scale)))
            sh = max(1, int(round(oh * scale)))
        else:
            sw, sh = ow, oh

        fg = im.resize((sw, sh), Image.Resampling.LANCZOS) if (sw, sh) != (ow, oh) else im.copy()

        # Use a blurred copy of the same source as background so the full original
        # content stays visible and we avoid hard black bars that may bias generation.
        bg = ImageOps.fit(im, (tw, th), method=Image.Resampling.LANCZOS)
        blur_radius = max(8, min(tw, th) // 28)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        # Slight darkening improves separation while preserving source colors.
        dark = Image.new("RGB", (tw, th), (0, 0, 0))
        bg = Image.blend(bg, dark, 0.10)

        x = (tw - fg.width) // 2
        y = (th - fg.height) // 2
        bg.paste(fg, (x, y))

        name = _normalized_name(a, tw, th, ".jpg")
        out_path = config.ASSET_DIR / name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not out_path.exists():
            bg.save(out_path, format="JPEG", quality=95, subsampling=0, optimize=True)

    digest = _sha256_file(out_path)
    b = dict(a)
    b.update({
        "stored_name": name,
        "path": str(out_path),
        "sha256": digest,
        "size_bytes": out_path.stat().st_size,
        "width": tw,
        "height": th,
        "mime": "image/jpeg",
        "remote_url": None,
        "remote_provider": None,
        "remote_object_key": None,
        "normalized_from_asset_id": a.get("id"),
        "auto_prepared": True,
    })
    return b, {
        "changed": True,
        "kind": "image",
        "original": [ow, oh],
        "prepared": [tw, th],
        "reasons": reasons or ["自动适配到 Seedance 2.5 素材范围"],
        "method": "保留原图完整内容 + 同源模糊背景补边",
        "path": str(out_path),
    }


def _video_canvas(w: int, h: int) -> tuple[int, int]:
    r = max(MIN_RATIO, min(MAX_RATIO, _ratio(w, h) or 1.0))
    # Use a moderate, valid working canvas for invalid videos. This keeps the
    # preprocessing reliable on normal PCs while satisfying pixel-count limits.
    if r >= 1.0:
        tw = 1280
        th = _safe_even(tw / r)
    else:
        th = 1280
        tw = _safe_even(th * r)
    # These extremes still stay above the documented minimum pixel count.
    return max(MIN_SIDE, tw), max(MIN_SIDE, th)


def normalize_video(asset: dict) -> tuple[dict, dict]:
    a = dict(asset or {})
    path = Path(a.get("path") or "")
    if not path.is_file():
        raise RuntimeError(f"参考视频本地文件不存在：{path}")

    probe = media.probe(path)
    w = int(probe.get("width") or a.get("width") or 0)
    h = int(probe.get("height") or a.get("height") or 0)
    fps = float(probe.get("fps") or a.get("fps") or 0)
    if not w or not h:
        raise RuntimeError(f"无法读取参考视频尺寸：{a.get('original_name') or path.name}")

    info = inspect_asset({**a, **probe})
    if not info.get("needs_fix"):
        return a, {
            "changed": False,
            "kind": "video",
            "original": [w, h],
            "prepared": [w, h],
            "reason": "原视频已符合 Seedance 2.5 素材限制",
        }

    ffmpeg = media.ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(
            "参考视频尺寸/比例不符合 Seedance 2.5 限制，但本机没有可用 FFmpeg，"
            "无法自动修正。请运行安装程序后重试。"
        )

    tw, th = _video_canvas(w, h)
    name = _normalized_name(a, tw, th, ".mp4")
    out_path = config.ASSET_DIR / name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    target_fps = min(VIDEO_MAX_FPS, max(VIDEO_MIN_FPS, fps or 30.0))
    target_fps = 30.0 if target_fps < 24.0 or target_fps > 60.0 else target_fps

    if not out_path.exists():
        # Blurred source background + contain foreground. This preserves all source
        # content while making the technical canvas legal.
        filt = (
            f"[0:v]split=2[bg0][fg0];"
            f"[bg0]scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},boxblur=20:2[bg];"
            f"[fg0]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            f"fps={target_fps:.3f},format=yuv420p[v]"
        )
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-filter_complex", filt,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
                si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
                kwargs["startupinfo"] = si
            except Exception:
                pass
        p = subprocess.run(cmd, capture_output=True, text=False, timeout=300, **kwargs)
        if p.returncode != 0 or not out_path.exists():
            err = (p.stderr or b"").decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError("参考视频自动适配失败：" + err)

    p2 = media.probe(out_path)
    digest = _sha256_file(out_path)
    b = dict(a)
    b.update({
        "stored_name": name,
        "path": str(out_path),
        "sha256": digest,
        "size_bytes": out_path.stat().st_size,
        "width": int(p2.get("width") or tw),
        "height": int(p2.get("height") or th),
        "fps": float(p2.get("fps") or target_fps),
        "duration": float(p2.get("duration") or a.get("duration") or 0),
        "has_audio": bool(p2.get("has_audio")),
        "mime": "video/mp4",
        "remote_url": None,
        "remote_provider": None,
        "remote_object_key": None,
        "normalized_from_asset_id": a.get("id"),
        "auto_prepared": True,
    })
    return b, {
        "changed": True,
        "kind": "video",
        "original": [w, h],
        "prepared": [b["width"], b["height"]],
        "reasons": info.get("reasons") or [],
        "method": "保留完整画面 + 同源模糊背景补边 + 本地转码",
        "path": str(out_path),
    }


def prepare_specs(specs: list[dict]) -> dict:
    """Prepare all provider-bound media locally before OSS/public upload.

    This step is local and free. If it cannot make a provider-safe asset, it raises
    before the paid video-create endpoint is called.
    """
    out = []
    fixes = []
    for index, spec in enumerate(specs or [], start=1):
        a = dict(spec.get("asset") or {})
        kind = a.get("kind")
        if kind == "image":
            prepared, report = normalize_image(a)
        elif kind == "video":
            prepared, report = normalize_video(a)
        else:
            prepared, report = a, {
                "changed": False,
                "kind": kind,
                "reason": "无需视觉尺寸预处理",
            }
        report["index"] = index
        report["role"] = spec.get("role")
        report["name"] = a.get("original_name") or a.get("stored_name")
        fixes.append(report)
        out.append({"asset": prepared, "role": spec.get("role")})
    return {"specs": out, "fixes": fixes}
