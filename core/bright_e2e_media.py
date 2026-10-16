# coding: utf-8
"""Read-only public original-post video download via yt-dlp.
No paid API submissions; local browser cookies ONLY after separately authorized.
"""
import importlib.util
import ipaddress
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import urllib.parse

ROOT=pathlib.Path(__file__).resolve().parents[1]
VENDOR=ROOT/'vendor'
if VENDOR.exists() and str(VENDOR) not in sys.path:
 sys.path.insert(0,str(VENDOR))

MAX_BYTES=120*1024*1024
MAX_DURATION=180
POST_RE=re.compile(r'^/@[A-Za-z0-9._-]+/video/(\d{15,25})/?$')


def clean_error(text):
 s=str(text)
 s=re.sub(r'https?://[^\s]+','[media-url-omitted]',s)
 s=re.sub(r'(?i)(cookie|authorization|token|signature|session)\s*[=:]\s*[^\s,;]+',r'\1=[redacted]',s)
 return s[-1300:]


def available():
 try:return importlib.util.find_spec('yt_dlp') is not None
 except (ImportError,ValueError,AttributeError):return False


def status():
 x={'installed':available(),'version':'','source':'vendor' if (VENDOR/'yt_dlp').exists() else 'python-environment'}
 if x['installed']:
  try:
   from yt_dlp.version import __version__
   x['version']=__version__
  except Exception:pass
 x['impersonation']=impersonation_status()
 return x


def valid_post_url(url):
 u=urllib.parse.urlsplit(str(url or '').strip())
 if u.scheme!='https' or u.hostname not in ('www.tiktok.com','tiktok.com','m.tiktok.com') or u.username or u.password or u.port not in (None,443):
  raise ValueError('只允许原始 https://www.tiktok.com/@账号/video/帖子ID 链接')
 m=POST_RE.fullmatch(u.path)
 if not m:raise ValueError('该帖子链接不是视频帖子地址')
 return urllib.parse.urlunsplit(('https','www.tiktok.com',u.path,'','')),m.group(1)


def _execute(args,timeout):
 # Windows child process timeouts are bounded; do not relay cookie values into logs.
 environment=os.environ.copy()
 if (VENDOR/'yt_dlp').is_dir():
  environment['PYTHONPATH']=str(VENDOR)+os.pathsep+environment.get('PYTHONPATH','')
 return subprocess.run(args,capture_output=True,text=True,errors='replace',timeout=timeout,
                       stdin=subprocess.DEVNULL,check=False,env=environment)



def impersonation_status():
 """Probe the actual yt-dlp subprocess interpreter; importable is NOT enough."""
 result={'available':False,'reason':'','targets':[]}
 if not available():
  result['reason']='yt-dlp is not installed in the active Python environment'
  return result
 try:
  import curl_cffi
  result['curl_cffi_version']=str(getattr(curl_cffi,'__version__','unknown'))
  from curl_cffi import requests as _curl_requests
 except Exception as exc:
  result['reason']='curl_cffi is not importable by the active Python: '+str(exc)[:200]
  return result
 try:
  r=_execute([sys.executable,'-m','yt_dlp','--ignore-config','--list-impersonate-targets'],15)
 except (OSError, subprocess.TimeoutExpired) as exc:
  result['reason']='Unable to verify yt-dlp impersonation targets: '+str(exc)[:180]
  return result
 if r.returncode!=0:
  result['reason']='yt-dlp target check failed: '+clean_error(r.stderr or r.stdout)[:220]
  return result
 lines=(r.stdout or '').splitlines()
 for line in lines:
  # Some versions show a single "Chrome - curl_cffi" row, others multiple variants.
  if re.search(r'(?i)\b(chrome|edge|safari)\b',line) and 'curl_cffi' in line:
   result['targets'].append(line.strip()[:180])
 usable=[x for x in result['targets'] if not re.search(r'(?i)(\bnot available\b|\bunavailable\b|\bunsupported\b)',x)]
 result['available']=bool(usable)
 if not result['available']:
  result['reason']='curl_cffi was imported, but yt-dlp reported no usable browser impersonation target; '+ (' / '.join(result['targets'][:2]) or 'no target advertised')
 return result


def assert_downloader_ready():
 stat=impersonation_status()
 if not stat['available']:
  raise ValueError('TikTok下载环境不完整：'+stat['reason']+'。请关闭工作台后重新运行 01_INSTALL.cmd，再检查 install_logs/dependency_report.json。')
 return stat


def download_post(post_url,expected_id,working_dir,mode='public',ffmpeg=None,runner=None):
 url,url_id=valid_post_url(post_url)
 if expected_id and str(expected_id)!=url_id:raise ValueError('采集post_id与原始视频链接编号不匹配')
 if mode not in ('public','chrome','edge'):raise ValueError('不支持的下载模式')
 if runner is None:assert_downloader_ready()
 work=pathlib.Path(working_dir).resolve();work.mkdir(parents=True,exist_ok=True)
 # Filename is constant under an internally created directory; never use external titles as paths.
 target=str(work/'original.%(ext)s')
 cmd=[sys.executable,'-m','yt_dlp','--ignore-config','--no-playlist','--no-progress',
      '--no-cache-dir','--no-write-info-json','--no-write-thumbnail','--no-write-subs',
      '--no-write-auto-subs','--no-write-comments',
      '--socket-timeout','20','--retries','1','--fragment-retries','1',
      '--max-filesize','120M','--format','best[ext=mp4]/best',
      '--print','after_move:%(id)s|||%(filepath)s','--output',target]
 if ffmpeg and pathlib.Path(ffmpeg).exists():cmd.extend(['--ffmpeg-location',str(pathlib.Path(ffmpeg).parent)])
 if mode!='public':cmd+=['--cookies-from-browser',mode]
 cmd+=['--',url]
 try:r=(runner or _execute)(cmd,MAX_DURATION)
 except subprocess.TimeoutExpired:raise ValueError('原帖下载超过180秒，已终止；不应反复自动重试')
 if r.returncode!=0:raise ValueError('原帖下载失败('+mode+'): '+clean_error(r.stderr or r.stdout)[-620:])
 lines=[s.strip() for s in (r.stdout or '').splitlines() if '|||' in s]
 if not lines:raise ValueError('下载器没有返回可核验的视频编号与文件路径')
 match=lines[-1].split('|||',1)
 found_id=re.sub(r'[^0-9]','',match[0].split(':')[-1])
 if found_id!=url_id:raise ValueError('下载器返回的TikTok视频ID与原帖ID不一致，拒绝使用')
 video_path=pathlib.Path(match[1].strip().strip('"')).resolve()
 if not video_path.is_relative_to(work) or not video_path.is_file() or video_path.is_symlink():
  raise ValueError('下载文件不在专属任务目录，拒绝访问')
 if video_path.suffix.lower() not in ('.mp4','.webm','.mkv','.mov'):
  raise ValueError('非支持的视频文件类型')
 size=video_path.stat().st_size
 if not 1500<=size<=MAX_BYTES:raise ValueError('下载视频大小超出安全范围')
 return {'path':str(video_path),'bytes':size,'extracted_id':found_id,'mode':mode}
