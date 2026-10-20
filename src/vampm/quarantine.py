"""Inventory and explicitly confirmed deletion, confined to the selected library's quarantine."""
import os
import stat
import threading
import uuid

from .util import atomic_json, read_json, safe_path, stamp


class Quarantine:
    def __init__(self, catalog, operations):
        self.c, self.ops = catalog, operations
        self.plans = {}
        self.lock = threading.Lock()

    def root(self):
        self.c.storage.check_pending()
        if self.c.storage.value['locations']['quarantine'] == 'game':
            self.c.storage.ensure_base()
        root = self.c.roots['quarantine']
        if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
            raise ValueError('隔离区不能是符号链接或目录联接。')
        return root

    @staticmethod
    def version(path):
        s = path.stat()
        if not stat.S_ISREG(s.st_mode): raise ValueError('仅能清理普通文件。')
        return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino]

    def inventory(self, job=None):
        root = self.root()
        owners, records, files, skipped = {}, {}, [], 0
        if not root.exists(): return files, records, skipped
        for row in self.ops.history():
            if row['kind'] != 'dedup': continue
            try:
                path = safe_path(self.ops.directory, row['id'] + '/manifest.json')
                record = read_json(path)
                records[row['id']] = record
                for move in record.get('moves', []):
                    if move.get('to_area') == 'quarantine':
                        owners[move['to_rel']] = (row['id'], move)
            except (ValueError, OSError, TypeError): continue
        def failed(error):
            raise ValueError('无法完整读取隔离区，请检查文件权限后重试。') from error
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=failed):
            if job: job.check()
            for name in list(dirs):
                try: safe_path(root, (os.path.relpath(os.path.join(directory, name), root)).replace(os.sep, '/'))
                except ValueError:
                    dirs.remove(name); skipped += 1
            for name in names:
                rel = os.path.relpath(os.path.join(directory, name), root).replace(os.sep, '/')
                try:
                    path = safe_path(root, rel)
                    version = self.version(path)
                except FileNotFoundError: continue  # A concurrent restore may finish during a read.
                except ValueError:
                    skipped += 1; continue
                owner, move = owners.get(rel, ('untracked', {}))
                files.append({'rel': rel, 'size': version[0], 'version': version, 'batch': owner,
                              'original': move.get('from_rel', ''), 'area': move.get('from_area', '')})
        files.sort(key=lambda f: f['rel'])
        return files, records, skipped

    def listing(self, batch=None, page=1):
        files, records, skipped = self.inventory()
        groups = {}
        for file in files:
            ident = file['batch']
            record = records.get(ident, {})
            group = groups.setdefault(ident, {'id': ident, 'created': record.get('created', ''),
                'count': 0, 'bytes': 0, 'can_restore': record.get('status') in ('done', 'running', 'interrupted', 'undoing')
                    and not record.get('irreversible'), 'irreversible': bool(record.get('irreversible'))})
            group['count'] += 1; group['bytes'] += file['size']
        result = {'count': len(files), 'bytes': sum(f['size'] for f in files), 'skipped': skipped,
                  'path': str(self.root()), 'batches': sorted(groups.values(), key=lambda r: r['created'], reverse=True)}
        if batch is not None:
            selected = [f for f in files if f['batch'] == batch]
            page = min(max(1, int(page)), max(1, (len(selected) + 79) // 80))
            result.update(items=[self.public_file(f) for f in selected[(page - 1) * 80:page * 80]],
                          total=len(selected), page=page)
        return result

    @staticmethod
    def public_file(file):
        return {k: v for k, v in file.items() if k != 'version'}

    def preview(self, data, job):
        self.ops.check_pending()
        files, records, skipped = self.inventory(job)
        ids = data.get('ids')
        if data.get('all') is not True:
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
                raise ValueError('请选择需要清理的隔离批次。')
            if set(ids) - {f['batch'] for f in files}: raise ValueError('隔离区已变化，请刷新后重新选择。')
            files = [f for f in files if f['batch'] in ids]
        if not files: raise ValueError('没有可清理的隔离文件。')
        ident = uuid.uuid4().hex
        owners = {f['batch'] for f in files} - {'untracked'}
        versions = {key: self.version(safe_path(self.ops.directory, key + '/manifest.json')) for key in owners}
        plan = {'id': ident, 'files': files, 'owners': versions, 'created': stamp()}
        with self.lock:
            while len(self.plans) >= 8: self.plans.pop(next(iter(self.plans)))
            self.plans[ident] = plan
        return {'quarantine_preview': {'id': ident, 'count': len(files), 'bytes': sum(f['size'] for f in files),
                'batch_count': len({f['batch'] for f in files}), 'skipped': skipped,
                'files': [self.public_file(f) for f in files[:80]]}}

    def purge(self, ident, confirmed, job):
        if confirmed is not True: raise ValueError('请先确认永久删除隔离文件。')
        self.ops.check_pending()
        with self.lock: plan = self.plans.pop(ident, None)
        if not plan: raise ValueError('清理预览已过期，请重新预览。')
        root = self.root()
        # Validate the entire snapshot before marking any operation irreversible.
        for key, version in plan['owners'].items():
            job.check()
            if self.version(safe_path(self.ops.directory, key + '/manifest.json')) != version:
                raise ValueError('恢复记录已变化，请重新预览。')
        for file in plan['files']:
            job.check()
            if self.version(safe_path(root, file['rel'])) != file['version']:
                raise ValueError('隔离文件已变化，请重新预览：' + file['rel'])
        job.check()
        manifest = safe_path(self.ops.directory, ident + '/manifest.json')
        state = {'id': ident, 'kind': 'purge_quarantine', 'created': stamp(), 'status': 'purging',
                 'irreversible': True, 'count': len(plan['files']), 'bytes': sum(f['size'] for f in plan['files']),
                 'deleted_count': 0, 'deleted_bytes': 0, 'files': plan['files']}
        atomic_json(manifest, state)
        removed, marked = [], set()
        try:
            for index, file in enumerate(plan['files']):
                job.check()
                path = safe_path(self.root(), file['rel'])
                if self.version(path) != file['version']:
                    raise ValueError('清理期间文件发生变化，剩余文件保留：' + file['rel'])
                # Mark only batches actually reached; untouched batches stay restorable on cancellation.
                key = file['batch']
                if key != 'untracked' and key not in marked:
                    source = safe_path(self.ops.directory, key + '/manifest.json')
                    if self.version(source) != plan['owners'][key]:
                        raise ValueError('恢复记录已变化，剩余文件保留。')
                    record = read_json(source)
                    record.update(irreversible=True, quarantine_cleanup=ident)
                    atomic_json(source, record)
                    marked.add(key)
                if index % 64 == 0:
                    self.ops._events(manifest.parent / 'events.jsonl', [
                        {'phase': 'delete_intent', 'rel': row['rel']} for row in plan['files'][index:index + 64]])
                path.unlink()
                removed.append(file['rel'])
                state['deleted_count'] += 1; state['deleted_bytes'] += file['size']
                job.update(state['deleted_count'], state['count'], file['rel'])
                parent = path.parent
                while parent != root:
                    try: parent.rmdir()
                    except OSError: break
                    parent = parent.parent
            state['status'] = 'done'
        except BaseException:
            state['status'] = 'partial'
            raise
        finally:
            state['finished'] = stamp()
            try:
                # No scan, label changes, or writes to either live package library.
                with self.c.db() as db:
                    for rel in removed:
                        for table in ('file_warnings', 'favorite_origins'):
                            db.execute(f"DELETE FROM {table} WHERE file_id IN (SELECT id FROM files WHERE area='quarantine' AND rel=?)", (rel,))
                        db.execute("DELETE FROM files WHERE area='quarantine' AND rel=?", (rel,))
            finally:
                atomic_json(manifest, state)
                self.c.invalidate()
        return {'kind': 'purge_quarantine', 'count': state['deleted_count'], 'bytes': state['deleted_bytes']}
