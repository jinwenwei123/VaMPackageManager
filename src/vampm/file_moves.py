"""No-overwrite moves; cross-volume copies are verified and recoverable.

The receipt is durable before publication. A crash can leave two copies, never
delete the source before the verified destination exists. Recovery only acts on
the recorded temporary object and the exact source/destination identities.
"""
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path

from .util import atomic_json, read_json, safe_path, sha256

PREFIX = '.vampm-transfer-'


def _check(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('不移动符号链接或目录联接：' + str(path))


def _version(path):
    _check(path)
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) and not stat.S_ISDIR(s.st_mode):
        raise ValueError('仅支持普通文件和目录：' + str(path))
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino]


def _identity(path):
    return _version(path)[3:]


def _rename(source, target):
    """Atomic no-replace publication. Never use POSIX os.rename to overwrite."""
    if target.exists() or target.is_symlink():
        raise FileExistsError(errno.EEXIST, '目标已存在', str(target))
    if os.name == 'nt':
        os.rename(source, target)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) == 0:
            return
        error = ctypes.get_errno()
        if error not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
            raise OSError(error, os.strerror(error), str(target))
    # Some mounted filesystems lack renameat2 but support exclusive hard links.
    if source.is_file():
        os.link(source, target, follow_symlinks=False)
        try: source.unlink()
        except BaseException:
            if _identity(source) == _identity(target): target.unlink()
            raise
    else:
        raise OSError(errno.ENOTSUP, '文件系统不支持无覆盖目录重命名', str(target))


def inventory(root, job=None):
    """Fingerprint a tree without following aliases; retain empty directories."""
    _check(root)
    if root.is_file(): return {'directory': False, 'dirs': [], 'files': {'': _version(root)}}
    if not root.is_dir(): raise FileNotFoundError(str(root))
    dirs, files = [''], {}
    def fail(error): raise error
    for directory, children, names in os.walk(root, followlinks=False, onerror=fail):
        if job: job.check()
        for name in sorted(children):
            path = Path(directory) / name
            _check(path)
            dirs.append(path.relative_to(root).as_posix())
        for name in sorted(names):
            path = Path(directory) / name
            files[path.relative_to(root).as_posix()] = _version(path)
    return {'directory': True, 'dirs': sorted(dirs), 'files': dict(sorted(files.items()))}


def _path(root, relative):
    return safe_path(root, relative) if relative else root


def receipt_path(target):
    target = Path(target)
    key = hashlib.sha256(os.path.normcase(target.name).encode()).hexdigest()[:24]
    return target.parent / (PREFIX + key + '.json')


def _stage(target, record):
    name = record.get('temp', '')
    if not isinstance(name, str) or not re.fullmatch(r'\.vampm-transfer-[a-f0-9]{32}\.part', name):
        raise ValueError('跨盘转移的暂存记录无效，已有文件保留。')
    return safe_path(target.parent, name)


def _read(source, target):
    receipt = receipt_path(target)
    _check(receipt)
    record = read_json(receipt)
    if (not isinstance(record, dict) or record.get('version') != 1
            or record.get('source_name') != source.name or record.get('target_name') != target.name
            or record.get('phase') not in ('copying', 'ready')):
        raise ValueError('跨盘转移记录不匹配，未改动已有文件。')
    _stage(target, record)
    return receipt, record


def _copy(source, target, record, receipt, job):
    stage = _stage(target, record)
    tree = record['tree']
    if inventory(source, job) != tree:
        raise ValueError('复制期间源文件发生变化，原文件保留。')
    if tree['directory']:
        stage.mkdir(exist_ok=True)
        for relative in tree['dirs']:
            _path(stage, relative).mkdir(parents=True, exist_ok=True)
    hashes = {}
    for index, (relative, version) in enumerate(tree['files'].items()):
        if job:
            job.check()
            job.update(index, len(tree['files']), '跨盘复制并校验：' + (relative or source.name))
        original, copied = _path(source, relative), _path(stage, relative)
        # A hard-interrupted directory copy can reuse complete verified files.
        digest = None
        if copied.exists():
            _check(copied)
            if copied.is_file() and _version(copied)[:2] == version[:2]:
                expected = sha256(original, job)
                if sha256(copied, job) == expected: digest = expected
            if digest is None:
                if not copied.is_file(): raise ValueError('转移暂存位置已被其他内容占用。')
                copied.unlink()
        if digest is None:
            h = hashlib.sha256()
            with original.open('rb') as inp, copied.open('xb') as out:
                while block := inp.read(4 * 1024 * 1024):
                    if job: job.check()
                    out.write(block); h.update(block)
                out.flush(); os.fsync(out.fileno())
            shutil.copystat(original, copied, follow_symlinks=False)
            digest = h.hexdigest()
            if _version(copied)[:2] != version[:2] or sha256(copied, job) != digest:
                raise ValueError('目标文件校验或时间保留失败，原文件未删除。')
        hashes[relative] = digest
    if inventory(source, job) != tree:
        raise ValueError('复制期间源目录发生变化，原文件保留。')
    if tree['directory']:
        for relative in sorted(tree['dirs'], key=lambda x: x.count('/'), reverse=True):
            shutil.copystat(_path(source, relative), _path(stage, relative), follow_symlinks=False)
    record.update(phase='ready', hashes=hashes, target_identity=_identity(stage))
    atomic_json(receipt, record)
    if job: job.check()
    _rename(stage, target)


def _finish(source, target, record, receipt, job):
    tree = record['tree']
    if _identity(target) != record['target_identity']:
        raise ValueError('跨盘转移的目标已被替换，未删除源文件。')
    copied = inventory(target, job)
    if (copied['directory'] != tree['directory'] or copied['dirs'] != tree['dirs']
            or set(copied['files']) != set(tree['files'])):
        raise ValueError('跨盘转移的目标目录已变化，源文件保留。')
    for relative, version in tree['files'].items():
        if (copied['files'][relative][:2] != version[:2]
                or sha256(_path(target, relative), job) != record['hashes'][relative]):
            raise ValueError('跨盘转移的目标校验失败，源文件保留。')
    if source.exists():
        _check(source)
        if _identity(source) != record['source_identity']:
            raise ValueError('跨盘转移的源文件已被替换，未继续删除。')
        remaining = inventory(source, job)
        if (remaining['directory'] != tree['directory']
                or set(remaining['dirs']) - set(tree['dirs'])
                or any(tree['files'].get(rel) != v for rel, v in remaining['files'].items())):
            raise ValueError('跨盘转移的源目录已变化，未继续删除。')
        for relative, version in remaining['files'].items():
            if job: job.check()
            path = _path(source, relative)
            if _version(path) != version: raise ValueError('源文件变化，转移已停止。')
            if _version(_path(target, relative)) != copied['files'][relative]:
                raise ValueError('目标文件变化，源文件保留。')
            path.unlink()
        if tree['directory']:
            for relative in sorted(remaining['dirs'], key=lambda x: (len(Path(x).parts), x), reverse=True):
                _path(source, relative).rmdir()
    receipt.unlink()


def _abort(source, target, record, receipt):
    # Only discard an unpublished copy while the complete original still exists.
    if inventory(source) != record['tree']:
        raise ValueError('源文件已变化，转移暂存与记录保留。')
    stage = _stage(target, record)
    if stage.exists():
        _check(stage)
        if stage.is_dir(): shutil.rmtree(stage)
        else: stage.unlink()
    receipt.unlink()


def recover_move(source, target, job=None, *, finish=False):
    """Recover a caller-known move, never discover/delete arbitrary recorded paths."""
    source, target = Path(source), Path(target)
    if not receipt_path(target).exists(): return False
    receipt, record = _read(source, target)
    stage = _stage(target, record)
    if record['phase'] == 'ready' and not stage.exists() and target.exists():
        _finish(source, target, record, receipt, job)
        return True
    if not finish:
        _abort(source, target, record, receipt)
        return False
    if target.exists(): raise ValueError('目标位置已有文件，转移记录保留。')
    if record['phase'] == 'copying': _copy(source, target, record, receipt, job)
    else:
        if _identity(stage) != record['target_identity']:
            raise ValueError('转移暂存已被替换，原文件保留。')
        if inventory(source, job) != record['tree']: raise ValueError('源文件变化，原文件保留。')
        _rename(stage, target)
    _finish(source, target, record, receipt, job)
    return True


def move_no_replace(source, target, job=None):
    source, target = Path(source), Path(target)
    if job: job.check()
    _check(source); _check(target)
    if target.exists(): raise ValueError('目标位置已有文件：' + str(target))
    if source.is_dir() and target.resolve().is_relative_to(source.resolve()):
        raise ValueError('不能将目录移入自身。')
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = receipt_path(target)
    if receipt.exists():
        raise ValueError('此位置有未完成的跨盘转移，请先恢复操作或继续迁移。')
    try:
        _rename(source, target)
        return
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, 'winerror', None) != 17: raise
    tree = inventory(source, job)
    if shutil.disk_usage(target.parent).free < sum(v[0] for v in tree['files'].values()) + 1024 * 1024:
        raise ValueError('目标磁盘空间不足，原文件未移动。')
    record = {'version': 1, 'phase': 'copying', 'source_name': source.name, 'target_name': target.name,
              'source_identity': _identity(source), 'temp': PREFIX + uuid.uuid4().hex + '.part', 'tree': tree}
    # Exclusive intent creation also prevents two writers from claiming one target.
    with receipt.open('x', encoding='utf-8') as out:
        json.dump(record, out, ensure_ascii=False)
        out.flush(); os.fsync(out.fileno())
    try:
        _copy(source, target, record, receipt, job)
        _finish(source, target, record, receipt, job)
    except Exception:
        # Published copies retain their receipt so undo/restart can finish safely.
        if _stage(target, record).exists() or not target.exists():
            _abort(source, target, record, receipt)
        raise
