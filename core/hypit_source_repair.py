"""One audited syntax correction for model-authored official vocabulary imports.

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
    history = project/'.agent_backups'
    history.mkdir(exist_ok=True)
    if len(list(history.iterdir())) >= 200:
        raise PermissionError('Source backup limit reached')
    backup = history/('import-repair-'+uuid.uuid4().hex+'.bak')
    with backup.open('xb') as stream:
        stream.write(original.encode('utf8'));stream.flush();os.fsync(stream.fileno())
    fd, name = tempfile.mkstemp(prefix='import-repair-',suffix='.tmp',dir=project)
    try:
        with os.fdopen(fd,'w',encoding='utf8',newline='') as stream:
            stream.write(updated);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
    return {'kind':'official_vocabulary_source_to_from','modules':changed,
        'before_sha256':hashlib.sha256(original.encode('utf8')).hexdigest(),
        'after_sha256':hashlib.sha256(updated.encode('utf8')).hexdigest(),
        'backup':backup.relative_to(project).as_posix(), 'paid_calls':0}
