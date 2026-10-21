# coding: utf-8
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import bright_e2e_media,media
x=bright_e2e_media.status()
print('yt-dlp installed:',x['installed'],'version:',x.get('version',''))
print('curl_cffi available:',x['impersonation']['available'])
print('check:',x['impersonation'].get('reason') or 'passed')
print('FFmpeg:', bool(media.ffmpeg_path()), 'FFprobe:',bool(media.ffprobe_path()))
print('No TikTok or OpenLux network request was made.')
raise SystemExit(0 if x['impersonation']['available'] and media.ffprobe_path() else 10)
