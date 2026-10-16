"""Restrictive local-source policy before a genuine compiler can resolve imports/assets.

This is an input boundary, not a sandbox for arbitrary third-party packages. Project
component code remains disabled until a separate trusted package review is available.
"""
import html
import json
import re
from pathlib import Path

MODULES = {'markup', 'markup/run', 'text', 'blob', 'media', 'media-operations', 'timeline',
    'timeline-author', 'temporal', 'spatial', 'film', 'html-video', 'visual-track', 'audio-track',
    'composition', 'seedance', 'generation', 'script', 'narrative', 'caption', 'narrative-caption'}
SUFFIXES = {'.svml', '.svrun', '.svs', '.mp4', '.mov', '.webm', '.png', '.jpg', '.jpeg',
    '.webp', '.wav', '.mp3', '.m4a', '.ttf', '.woff', '.woff2'}
ATTR = re.compile(r'\b(?:src|source|from)\s*=\s*([\'"])(.*?)\1', re.S)

def validate_project_boundary(project: Path):
    root = Path(project).resolve(strict=True)
    manifest = root / 'package.json'
    if not manifest.is_file() or manifest.is_symlink():
        raise PermissionError('Managed Hypit project manifest required')
    if manifest.stat().st_size > 64000:
        raise PermissionError('Project manifest too large')
    package = json.loads(manifest.read_text(encoding='utf-8'))
    if package.get('hypit') != {'project': True}:
        raise PermissionError('Agent project cannot select executable package facets')
    for field in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'workspaces', 'imports'):
        if package.get(field):
            raise PermissionError('Agent project packages require separate trusted code review')
    count = 0
    for source in root.rglob('*'):
        rel = source.relative_to(root)
        if any(p in {'node_modules', '.git', '.hypit', '.agent_backups'} for p in rel.parts):
            continue
        if source.suffix not in {'.svml', '.svrun', '.svs'} or not source.is_file():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise PermissionError('Compiler source escapes project')
        count += 1
        if count > 500 or source.stat().st_size > 192000:
            raise PermissionError('Compiler source exceeds limits')
        text = html.unescape(source.read_text(encoding='utf-8'))
        if re.search(r'url\s*\(|@import|<script\b|javascript:', text, re.I) or (source.suffix == '.svs' and '\\' in text):
            raise PermissionError('External CSS or executable source content forbidden')
        for _, value in ATTR.findall(text):
            if value.startswith('@hypit/'):
                if value[7:].removesuffix('@1') not in MODULES or not value.endswith('@1'):
                    raise PermissionError('Compiler package is outside approved official vocabulary')
                continue
            if ':' in value or '\\' in value or Path(value).is_absolute():
                raise PermissionError('Compiler external resource forbidden')
            if not value.startswith('./'):
                raise PermissionError('Compiler resources must be explicit local project paths')
            target = source.parent / value
            if '..' in Path(value).parts or target.suffix not in SUFFIXES or not target.resolve().is_relative_to(root):
                raise PermissionError('Compiler resource escapes approved project boundary')
            if not target.is_file() or target.is_symlink():
                raise PermissionError('Compiler resource missing or symbolic')
