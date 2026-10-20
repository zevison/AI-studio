"""Bounded syntax corrections backed by genuine official compiler diagnostics.

Does not invent shots, timing, assets or a template. Requires the corresponding
genuine compiler diagnostic; preserves the original source and repair hashes.
"""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid
from .hypit_cli_guard import MODULES, validate_project_boundary


def repair_vocabulary_imports(project, diagnostic):
    project = Path(project)
    message = (diagnostic or {}).get('error',{}).get('message','')
    if not re.fullmatch(r'@hypit/[A-Za-z0-9_./-]+@1 must be one exact npm package name',message):
        return None
    validate_project_boundary(project)
    path = project/'main.svml'
    original = path.read_text(encoding='utf8')
    changed = []
    def rewrite(match):
        tag = match[0]
        attribute = re.search(r'\bsource\s*=\s*([\'"])(@hypit/([^\'"<>]+)@1)\1',tag)
        if not attribute or attribute[3] not in MODULES or re.search(r'\bfrom\s*=',tag):
            return tag
        changed.append(attribute[2])
        return tag[:attribute.start()] + re.sub(r'^source','from',attribute[0]) + tag[attribute.end():]
    updated = re.sub(r'<import\b[^<>]*/>',rewrite,original)
    if not changed:
        return None
    return _save_repair(project, original, updated, 'official_vocabulary_source_to_from', modules=changed)


def _save_repair(project, original, updated, kind, **details):
    path = project/'main.svml'
    history = project/'.agent_backups'
    history.mkdir(exist_ok=True)
    if len(list(history.iterdir())) >= 200:
        raise PermissionError('Source backup limit reached')
    backup = history/('syntax-repair-'+uuid.uuid4().hex+'.bak')
    with backup.open('xb') as stream:
        stream.write(original.encode('utf8'));stream.flush();os.fsync(stream.fileno())
    fd, name = tempfile.mkstemp(prefix='syntax-repair-',suffix='.tmp',dir=project)
    try:
        with os.fdopen(fd,'w',encoding='utf8',newline='') as stream:
            stream.write(updated);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
    return {'kind':kind, **details,
        'before_sha256':hashlib.sha256(original.encode('utf8')).hexdigest(),
        'after_sha256':hashlib.sha256(updated.encode('utf8')).hexdigest(),
        'backup':backup.relative_to(project).as_posix(), 'paid_calls':0}


def repair_timeline_origin(project, diagnostic):
    """Only replace invalid literal zero origins with the official start Instant.

    Never guess nonzero timing, rewrite text/CDATA, or edit another vocabulary.
    Preserve the original and require another real check/plan after correction.
    """
    if (diagnostic or {}).get('error', {}).get('message') != 'Timeline expression 0 is not an Instant.':
        return None
    project = Path(project)
    validate_project_boundary(project)
    original = (project/'main.svml').read_text(encoding='utf8')
    masked = re.sub(r'<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>',
                    lambda m: ' ' * len(m[0]), original)
    tags = list(re.finditer(r'<([A-Za-z_][\w:.-]*)\b(?:[^\"\'<>]|\"[^\"]*\"|\'[^\']*\')*>', masked))
    def attributes(tag):
        # Consume whole quoted values, so embedded from="0" in a label is text.
        return list(re.finditer(r'([A-Za-z_][\w:.-]*)\s*=\s*(\"[^\"]*\"|\'[^\']*\'|\{[^{}]*\})', tag))
    imports = {}
    for match in tags:
        if match[1] != 'import':
            continue
        attrs = {a[1]: a[2][1:-1] for a in attributes(match[0])}
        alias = attrs.get('as')
        if alias:
            imports.setdefault(alias, []).append(attrs.get('from'))
    aliases = {alias for alias, values in imports.items() if values == ['@hypit/timeline-author@1']}
    replacements = []
    for match in tags:
        if match[1] not in {alias + ':Window' for alias in aliases}:
            continue
        origins = [a for a in attributes(match[0]) if a[1] == 'from']
        if len(origins) == 1 and origins[0][2] in {'"0"', "'0'"}:
            attr = origins[0]
            replacements.append((match.start() + attr.start(2) + 1, match.start() + attr.end(2) - 1))
    if not replacements:
        return None
    updated = original
    for start, end in reversed(replacements):
        updated = updated[:start] + 'start' + updated[end:]
    return _save_repair(project, original, updated, 'official_timeline_zero_to_start', windows=len(replacements))
