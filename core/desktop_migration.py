"""Explicit, non-destructive migration of a stopped legacy installation.

No configuration import, credential access, subprocess or network operation is
performed here. Call before starting the destination backend. Old installations
remain untouched; existing destination data is never merged or overwritten.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath, PurePosixPath
import shutil
import sqlite3
import tempfile
import time
import uuid

from .sqlite_snapshot import snapshot_database

FORMAT = 'seedance.desktop-migration@1'
BUSY = frozenset(('pending', 'running', 'result_pending', 'submission_unknown',
                 'in_flight', 'calling', 'submitting', 'submitted', 'authoring',
                 'building', 'validating', 'exporting', 'paid_result_unknown', 'unknown'))
DATABASE_SUFFIXES = {'.db', '.sqlite', '.sqlite3'}
EXCLUDED_DIRS = {'cache', 'backups', '.trash', 'logs', 'install_logs'}
EXCLUDED_NAMES = {'secrets.json', 'secrets.dpapi', 'profile.json', 'configuration.json',
                  'provider_secrets.dpapi', 'provider_secrets.json', '.desktop-migration.json'}


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _plain(path: Path) -> Path:
    path = path.absolute()
    if any(_linked(item) for item in (path, *path.parents)):
        raise PermissionError('迁移路径包含链接或目录联接，已停止')
    return path.resolve()


def _digest(path: Path, check=None) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if check is not None:
                check()
            value.update(chunk)
    return value.hexdigest()


def _json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.migration-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump(doc, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _stopped(root: Path) -> None:
    markers = [root / 'seedance_studio.pid', *(root / 'data/workspaces').glob('*/seedance_studio.pid')]
    for marker in markers:
        if not marker.exists():
            continue
        if _linked(marker):
            raise PermissionError('进程标记为链接')
        try:
            pid = int(marker.read_text(encoding='ascii').strip())
            if pid <= 0:
                raise ValueError()
        except (OSError, ValueError):
            raise PermissionError('旧版运行状态无法核实，请先关闭工作台') from None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                kernel.CloseHandle(handle)
                raise PermissionError('旧版进程仍存在，请先关闭所有工作台')
            if ctypes.get_last_error() != 87:
                raise PermissionError('无法核实旧版进程权限，请先关闭所有工作台')
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                raise PermissionError('无法核实旧版进程权限') from None
            raise PermissionError('旧版进程仍存在，请先关闭所有工作台')


def _check_database(path: Path, max_schema_version: int, *, require_studio=False) -> None:
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    started = time.monotonic()
    conn.set_progress_handler(lambda: int(time.monotonic() - started > 60), 10000)
    try:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('旧数据库完整性检查未通过')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if require_studio and not {'assets', 'tasks'}.issubset(tables):
            raise ValueError('数据库缺少 Seedance 素材及任务表，不能认定为旧版工作台')
        if 'app_meta' in tables:
            row = conn.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
            if row and (not str(row[0]).isdigit() or int(row[0]) > max_schema_version):
                raise ValueError('旧数据库版本高于本程序或无法识别，禁止降级迁移')
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {r[1] for r in conn.execute('PRAGMA table_info(' + quoted + ')')}
            for name in ('status', 'state', 'phase'):
                if name in columns:
                    query = 'SELECT 1 FROM ' + quoted + ' WHERE lower("' + name + '") IN (' + ','.join('?' for _ in BUSY) + ') LIMIT 1'
                    if conn.execute(query, tuple(BUSY)).fetchone():
                        raise PermissionError('有运行中或结果不明的任务，请先在旧版核对；不会自动重提付费请求')
    finally:
        conn.close()


def schema_version() -> int:
    """Read the immutable source contract without importing application state."""
    tree = ast.parse(Path(__file__).with_name('db.py').read_text(encoding='utf8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'CURRENT_SCHEMA_VERSION' for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, int) and value >= 0:
                return value
    raise ValueError('无法读取本版本数据库 schema 合同')


def inspect_legacy(source_root, *, max_schema_version=None) -> dict:
    max_schema_version = schema_version() if max_schema_version is None else max_schema_version
    root = _plain(Path(source_root))
    data = _plain(root / 'data')
    if not data.is_dir() or not (data / 'studio.db').is_file():
        raise ValueError('请选择包含 data/studio.db 的旧版工作台目录')
    _stopped(root)
    _plain(data / 'studio.db')
    _check_database(data / 'studio.db', max_schema_version, require_studio=True)
    return {'root': str(root), 'data_dir': str(data), 'has_database': True,
            'credentials': '不复制明文密钥；同一 Windows 用户沿用现有 DPAPI 凭据，否则请重新授权'}


def discover_candidates(search_roots, *, limit=20) -> list[dict]:
    """Inspect only explicitly supplied folders and their immediate children."""
    if not 1 <= limit <= 100:
        raise ValueError('候选数量上限必须在 1–100 之间')
    results = []
    seen = set()
    for entry in search_roots:
        try:
            base = _plain(Path(entry))
            candidates = [base]
            if base.is_dir():
                with os.scandir(base) as entries:
                    for index, item in enumerate(entries):
                        if index >= 1000:
                            break
                        if item.is_dir(follow_symlinks=False):
                            candidates.append(Path(item.path))
            for candidate in candidates:
                if _linked(candidate) or str(candidate).casefold() in seen:
                    continue
                seen.add(str(candidate).casefold())
                database = candidate / 'data/studio.db'
                if database.is_file() and not _linked(database) and not _linked(database.parent):
                    results.append({'root': str(candidate), 'data_dir': str(database.parent), 'has_database': True})
                    if len(results) >= limit:
                        return results
        except (OSError, PermissionError):
            continue
    return results


def _rebase_database(path, source, target, stage):
    """Rebase whole local paths, including JSON values; never replace substrings."""
    flavor = PureWindowsPath if PureWindowsPath(str(source)).drive else PurePosixPath
    prefix = flavor(str(source))
    def rebase(value):
        if isinstance(value, dict):
            return {k: rebase(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rebase(v) for v in value]
        if not isinstance(value, str):
            return value
        try:
            relative = flavor(value).relative_to(prefix)
        except ValueError:
            return value
        if not relative.parts or '..' in relative.parts or not stage.joinpath(*relative.parts).exists():
            return value
        return str(target.joinpath(*relative.parts))
    conn = sqlite3.connect(path)
    changed = 0
    try:
        conn.execute('BEGIN IMMEDIATE')
        tables = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for table, sql in tables:
            quote = lambda value: '"' + value.replace('"', '""') + '"'
            columns = conn.execute('PRAGMA table_info(' + quote(table) + ')').fetchall()
            text_columns = [r[1] for r in columns if 'TEXT' in str(r[2]).upper()]
            if not text_columns:
                continue
            keys = [r[1] for r in sorted(columns, key=lambda item: item[5]) if r[5]] if 'WITHOUT ROWID' in (sql or '').upper() else ['rowid']
            fields = keys + text_columns
            for row in conn.execute('SELECT ' + ','.join(map(quote, fields)) + ' FROM ' + quote(table)).fetchall():
                updates = {}
                for column, original in zip(text_columns, row[len(keys):]):
                    updated = rebase(original)
                    if isinstance(original, str) and original.lstrip().startswith(('{', '[')):
                        try:
                            decoded = json.loads(original)
                            replacement = rebase(decoded)
                            if replacement != decoded:
                                updated = json.dumps(replacement, ensure_ascii=False, separators=(',', ':'))
                        except (ValueError, RecursionError):
                            pass
                    if updated != original:
                        updates[column] = updated
                if updates:
                    conn.execute('UPDATE ' + quote(table) + ' SET ' + ','.join(quote(k) + '=?' for k in updates) + ' WHERE ' + ' AND '.join(quote(k) + '=?' for k in keys), (*updates.values(), *row[:len(keys)]))
                    changed += 1
        if any(table == 'generation_previews' for table, _ in tables):
            conn.execute('UPDATE generation_previews SET expires_at=0')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return changed


def migrate_legacy(source_root, user_home, *, max_schema_version=None, fail_after=None, timeout_seconds=300) -> dict:
    """Publish one independent data tree atomically; retain all failure evidence."""
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError('迁移超时必须在 1–3600 秒之间')
    max_schema_version = schema_version() if max_schema_version is None else max_schema_version
    info = inspect_legacy(source_root, max_schema_version=max_schema_version)
    source = Path(info['data_dir'])
    home = _plain(Path(user_home))
    if source.parent.is_relative_to(home) or home.is_relative_to(source.parent):
        raise PermissionError('旧数据与目标目录必须互相独立')
    home.mkdir(parents=True, exist_ok=True)
    target = _plain(home / 'data')
    if target.exists():
        marker = _plain(target / '.desktop-migration.json')
        if marker.is_file():
            try:
                previous = json.loads(marker.read_text(encoding='utf8'))
                if (previous.get('format') == FORMAT and previous.get('source') == str(source.parent)
                        and previous.get('destination') == str(home)
                        and isinstance(previous.get('files'), list) and previous['files']):
                    for item in previous['files']:
                        relative = Path(item['path'])
                        if relative.is_absolute() or '..' in relative.parts:
                            raise PermissionError('迁移凭证中的文件路径越界')
                        file = _plain(target / relative)
                        if not file.is_file() or _digest(file) != item['sha256']:
                            raise ValueError('目标数据已变化；禁止重复迁移覆盖')
                    return {**previous, 'state': 'complete', 'reused': True, 'published_data': str(target)}
            except (KeyError, json.JSONDecodeError, TypeError):
                raise ValueError('目标迁移凭证无效，原数据保持不变') from None
        raise FileExistsError('目标已存在用户数据；禁止覆盖或自动合并，请使用空白用户目录')
    files = []
    skipped = 0
    total = 0
    started = time.monotonic()
    def deadline():
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError('迁移超时；旧版数据保持不变')
    for folder, dirs, names in os.walk(source, followlinks=False):
        deadline()
        parent = Path(folder)
        for name in dirs + names:
            item = _plain(parent / name)
            if not item.is_relative_to(source):
                raise PermissionError('数据目录存在越界文件')
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS]
        for name in names:
            path = parent / name
            if name in EXCLUDED_NAMES or name.startswith('.env') or name.endswith(('-wal', '-shm', '.log', '.pid')) or name.lower().endswith(('.dpapi', '.pem', '.key')):
                skipped += 1
                continue
            if path.stat().st_nlink > 1:
                raise PermissionError('迁移不接受多硬链接文件')
            files.append(path)
            total += path.stat().st_size
            if len(files) > 200000:
                raise ValueError('迁移文件数超过安全上限')
    if shutil.disk_usage(home).free < total + 256 * 1024 * 1024:
        raise OSError('目标磁盘空间不足')
    identifier = uuid.uuid4().hex
    stage = home / ('.mig-' + identifier[:12])
    stage.mkdir()
    journal = home / 'migrations' / (identifier + '.json')
    receipt = {'format': FORMAT, 'id': identifier, 'state': 'staging', 'source': str(source.parent),
               'destination': str(home), 'stage': str(stage), 'source_preserved': True,
               'copied_files': 0, 'excluded_files': skipped, 'credentials_copied': False,
               'credentials_notice': info['credentials'], 'rebased_rows': 0, 'files': []}
    _json(journal, receipt)
    try:
        for original in files:
            deadline()
            _plain(original)
            relative = original.relative_to(source)
            copy = stage / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            if original.suffix.lower() in DATABASE_SUFFIXES:
                _check_database(original, max_schema_version)
                snapshot_database(original, copy, timeout_seconds=min(60, timeout_seconds))
            else:
                before = original.stat()
                shutil.copy2(original, copy)
                after = original.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or _digest(original, deadline) != _digest(copy, deadline):
                    raise RuntimeError('源文件在迁移中发生变化，未发布目标数据')
            receipt['copied_files'] += 1
            if fail_after is not None and receipt['copied_files'] >= fail_after:
                raise OSError('Injected migration interruption')
        for path in stage.rglob('*'):
            deadline()
            if path.is_file() and path.suffix.lower() in DATABASE_SUFFIXES:
                receipt['rebased_rows'] += _rebase_database(path, source, target, stage)
                _check_database(path, max_schema_version)
        _stopped(source.parent)
        _check_database(source / 'studio.db', max_schema_version)
        for path in stage.rglob('*'):
            if path.is_file():
                receipt['files'].append({'path': path.relative_to(stage).as_posix(), 'sha256': _digest(path, deadline)})
        receipt['state'] = 'publishing'
        _json(journal, receipt)
        _plain(home)
        if target.exists():
            raise FileExistsError('迁移期间目标数据已出现，已停止发布')
        # Windows rename cannot replace an existing target. Retain a receipt in
        # the published tree so an interruption after rename is identifiable.
        _json(stage / '.desktop-migration.json', receipt)
        stage.rename(target)
        receipt.update(state='complete', published_data=str(target))
        _json(journal, receipt)
        return receipt
    except Exception as exc:
        receipt.update(state='failed_preserved', error_type=type(exc).__name__)
        try:
            _json(journal, receipt)
        except OSError:
            pass
        raise
