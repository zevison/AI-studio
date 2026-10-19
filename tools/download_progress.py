"""Stdlib HTTP downloader with observable, real-byte progress (no fake percentages).

Only public tool/runtime binaries should use this helper; it never handles API secrets.
"""
from __future__ import annotations

import datetime as dt
import time
import urllib.request
from pathlib import Path


class DownloadTimeout(TimeoutError):
    pass


class DownloadTooSlow(ConnectionError):
    """First source is too slow; the caller can try a different official mirror."""
    pass


def download_file(url: str, path: Path, *, label: str, max_seconds: int = 1200,
                  connect_timeout: int = 35, progress_interval: float = 2.0,
                  min_speed_mbps: float = 0.0, slow_grace_seconds: float = 50.0,
                  open_url=None, now=None, printer=print) -> int:
    """Download with real byte counts and progress; remove partial bytes on any failure.

    `open_url`, `now`, and `printer` are injectable to test it without a network.
    Server size may be missing: print transferred MB without inventing a percentage.
    HTTP/ socket reads can be blocked until connect_timeout. The displayed last line
    tells the user this in advance.
    """
    path=Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = now or time.monotonic
    if open_url is None:
        from trusted_download import open_public_url
        opener=open_public_url
    else:opener=open_url
    bytes_read=0
    begin=now()
    last=begin-progress_interval
    last_report_bytes=0
    req=urllib.request.Request(url, headers={'User-Agent': 'SeedanceStudio/5.0.0'})
    printer(f'  [{label}] Connecting to download server (up to {connect_timeout}s per network wait)...', flush=True)
    try:
        with opener(req, timeout=connect_timeout) as resp, path.open('wb') as output:
            connected_at=now()
            header=getattr(resp,'headers',{})
            try:total=int(header.get('Content-Length') or 0)
            except (TypeError,ValueError):total=0
            if total>0:
                printer(f'  [{label}] Expected size: {total/1048576:.1f} MB. Downloading...',flush=True)
            else:
                printer(f'  [{label}] Total size unavailable; showing actual received MB.',flush=True)
            while True:
                if now()-begin > max_seconds:
                    raise DownloadTimeout(f'{label}: download exceeded {max_seconds//60} minutes')
                chunk=resp.read(256*1024)
                if not chunk:break
                output.write(chunk)
                bytes_read+=len(chunk)
                elapsed=max(0.1,now()-begin)
                transfer_seconds=max(0.1,now()-connected_at)
                if min_speed_mbps>0 and transfer_seconds>=slow_grace_seconds:
                    measured_speed=(bytes_read/1048576)/transfer_seconds
                    if measured_speed<min_speed_mbps:
                        raise DownloadTooSlow(f'{label}: {measured_speed:.2f} MB/s for {int(transfer_seconds)}s; switching to alternate source')
                if now()-last >= progress_interval or bytes_read-last_report_bytes >= 5*1048576:
                    mb=bytes_read/1048576
                    speed=mb/elapsed
                    if total>0:
                        percent=min(100,int(bytes_read*100/total))
                        fill=min(20,percent//5)
                        bar='#'*fill + '-'*(20-fill)
                        remain=max(0.0,total/1048576-mb)
                        eta=round(remain/speed) if speed>0.01 else None
                        eta_text = str(dt.timedelta(seconds=eta)) if eta is not None else '--'
                        printer(f'  [{label}] [{bar}] {percent:3d}% | {mb:.1f}/{total/1048576:.1f} MB | {speed:.2f} MB/s | ETA {eta_text}',flush=True)
                    else:
                        printer(f'  [{label}] Received {mb:.1f} MB | {speed:.2f} MB/s | {int(elapsed)}s elapsed',flush=True)
                    last=now()
                    last_report_bytes=bytes_read
            output.flush()
            if total and bytes_read!=total:
                raise IOError(f'{label}: incomplete download ({bytes_read}/{total} bytes)')
            if bytes_read<200_000:
                raise IOError(f'{label}: unexpected tiny download ({bytes_read} bytes)')
            if total>0:
                printer(f'  [{label}] [####################] 100% | {bytes_read/1048576:.1f}/{total/1048576:.1f} MB verified',flush=True)
            printer(f'  [{label}] Transfer complete: {bytes_read/1048576:.1f} MB in {int(now()-begin)}s.',flush=True)
            return bytes_read
    except BaseException:
        path.unlink(missing_ok=True)
        raise
