"""Permanent packages share the index, but are excluded from ordinary file moves."""
import json
from collections import defaultdict
from pathlib import PurePosixPath

from .util import atomic_json, matches, read_json

BASE_FOLDER = '基础包'


class BasePackages:
    def __init__(self, catalog):
        self.c = catalog
        self.path = catalog.data / 'base-packages.json'

    @staticmethod
    def inside(rel):
        return rel.replace('\\', '/').casefold().startswith(BASE_FOLDER + '/')

    def state(self, rows=None):
        saved = read_json(self.path)
        if saved is not None:
            if not isinstance(saved, dict) or not isinstance(saved.get('roots'), list):
                raise ValueError('固定基础包配置无效，请恢复配置备份。')
            return {'roots': sorted(set(str(k).casefold() for k in saved['roots']))}
        # Adopt the distribution's existing base folder once. Saving an empty
        # roots list is intentional and must never re-adopt an unpinned package.
        return {'roots': sorted({r['pkgkey'] for r in (rows if rows is not None else self.c.rows())
                                 if self.inside(r['rel'])})}

    def save(self, state):
        atomic_json(self.path, state)
        self.c.invalidate()

    @staticmethod
    def resolve(packages, state):
        roots = set(state['roots'])
        chosen = roots.intersection(packages)
        parent = {}
        groups = defaultdict(list)
        for key, p in packages.items():
            if p['version'].isdigit() and not p['error']:
                groups[key.rsplit('.', 1)[0]].append(p)
        for group in groups.values():
            group.sort(key=lambda p: int(p['version']), reverse=True)
        cache, missing = {}, set()
        pending = sorted(chosen)
        for key in pending:
            # Fixed same-UID copies remain untouched. Follow references from
            # every readable copy rather than assuming VaM's selection order.
            refs = set()
            for row in packages[key]['files']:
                info = json.loads(row['info'])
                if not info.get('error'):
                    refs.update(info.get('deps', []))
            for ref in sorted(refs):
                rk = ref.casefold()
                if rk not in cache:
                    cache[rk] = next((p['key'] for p in groups.get(rk.rsplit('.', 1)[0], [])
                                      if matches(ref, p['pkgid'])), None)
                dep = cache[rk]
                if dep is None:
                    missing.add(ref)
                elif dep not in chosen:
                    chosen.add(dep); pending.append(dep); parent[dep] = key
        return {'keys': chosen, 'roots': roots, 'parent': parent, 'missing': sorted(missing)}

    def annotate(self, packages, rows):
        resolved = self.resolve(packages, self.state(rows))
        for key, p in packages.items():
            p['pinned'] = key in resolved['keys']
            p['pin_root'] = key in resolved['roots']
            p['pin_parent'] = resolved['parent'].get(key)

    @staticmethod
    def target(package, row):
        # Existing files under 基础包 never change their path, even if duplicate
        # variants are later added elsewhere in the library.
        if BasePackages.inside(row['rel']):
            return row['rel']
        parts = [BASE_FOLDER]
        if len(package['files']) > 1:
            parts.extend([package['pkgid'], '副本-' + str(row['id'])])
        return '/'.join(parts + [PurePosixPath(row['rel']).name])
