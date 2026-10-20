"""Select physical VAR copies without changing package names or archive contents."""
import collections
import json
import re
import threading

from .util import atomic_json, fingerprint, read_json, sha256
from .bundles import Bundles


class ActivationChoices:
    def __init__(self, catalog):
        self.c = catalog
        self.path = catalog.data / 'activation-choices.json'
        self._digests = {}
        self._lock = threading.Lock()

    @staticmethod
    def validate(mode, choices):
        if mode not in ('select', 'all'):
            raise ValueError('同名副本处理方式无效')
        if not isinstance(choices, dict) or len(choices) > 20000:
            raise ValueError('副本选择必须为包 key 与 SHA-256 的对应表')
        result = {}
        for key, value in choices.items():
            if not isinstance(key, str) or not key or len(key) > 500:
                raise ValueError('副本选择的包 key 无效')
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', value):
                raise ValueError('副本选择需要完整的 SHA-256')
            result[key.casefold()] = value.lower()
        return {'mode': mode, 'choices': result}

    def policy(self, profile_id=None):
        data = read_json(self.path, {})
        value = data.get('profiles', {}).get(profile_id, {}) if profile_id else data.get('selection', {})
        return self.validate(value.get('mode', 'select'), value.get('choices', {}))

    def save(self, policy, profile_id=None):
        policy = self.validate(policy['mode'], policy['choices'])
        if self.policy(profile_id) == policy:
            return
        data = read_json(self.path, {})
        if profile_id:
            data.setdefault('profiles', {})[profile_id] = policy
        else:
            data['selection'] = policy
        atomic_json(self.path, data)
        self.c.invalidate(packages=False)

    def forget(self, profile_id):
        if not self.path.exists():
            return
        data = read_json(self.path, {})
        if data.get('profiles', {}).pop(profile_id, None) is not None:
            atomic_json(self.path, data)

    def resolve(self, keys, policy, job=None, bundle_state=None):
        pkgs, resolve = self.c.packages(), self.c.resolver()
        folders=bundle_state if bundle_state is not None else Bundles(self.c).state()
        by_main={g['main_id']:g for g in folders['groups']}
        row_keys={r['id']:p['key'] for p in pkgs.values() for r in p['files']}
        pending = list(keys)
        included, missing, selected = set(), set(), {}
        conflicts, unresolved, keeps = [], [], []
        known_choices = policy['choices']
        repaired_hashes=read_json(self.c.data/'repaired-copy-hashes.json',{})
        effective_choices = dict(known_choices)
        while pending:
            if job:
                job.check()
            key = str(pending.pop()).casefold()
            if key in included:
                continue
            p = pkgs.get(key)
            if not p:
                missing.add(key)
                continue
            included.add(key)
            rows = p['files']
            info = {r['id']: json.loads(r['info']) for r in rows}
            variants = collections.defaultdict(list)
            if not p.get('pinned') and (len(rows) > 1 or key in known_choices):
                for row in rows:
                    path = self.c.path(row)
                    if fingerprint(path) != [row['size'], row['mtime']]:
                        raise ValueError('文件变化，请重新生成预览：' + row['rel'])
                    if row.get('sha') and row.get('sha_mtime') == row['mtime']:
                        digest = row['sha']
                    else:
                        if job:
                            job.message = '核对同名副本：' + p['pkgid']
                        cache_key = (str(path), row['size'], row['mtime'])
                        with self._lock:
                            digest = self._digests.get(cache_key)
                            if not digest:
                                digest = sha256(path, job)
                                if len(self._digests) > 2000:
                                    self._digests.clear()
                                self._digests[cache_key] = digest
                        if fingerprint(path) != [row['size'], row['mtime']]:
                            raise ValueError('副本校验期间文件变化：' + row['rel'])
                    variants[digest].append(row)
            readable = [r for r in rows if not info[r['id']].get('error')]
            chosen = rows if p.get('pinned') else readable[:1]
            if variants:
                valid = {digest: [r for r in files if not info[r['id']].get('error')]
                         for digest, files in variants.items()}
                valid = {digest: files for digest, files in valid.items() if files}
                digest = known_choices.get(key)
                seen=set()
                while digest and digest not in valid and digest not in seen:
                    seen.add(digest);replacement=repaired_hashes.get(key,{}).get(digest)
                    if not replacement:break
                    digest=replacement
                stale = bool(digest and digest not in valid)
                if policy['mode'] == 'all':
                    chosen = rows
                elif digest in valid:
                    chosen = valid[digest][:1]
                elif len(valid) == 1 and not stale:
                    digest = next(iter(valid))
                    chosen = valid[digest][:1]
                elif len(valid) > 1 or stale:
                    unresolved.append(key)
                    chosen = readable  # Provisional union reveals downstream conflicts.
                else:
                    chosen = []
                if digest in valid and policy['mode'] == 'select' and key not in unresolved:
                    effective_choices[key] = digest
                if len(rows) > 1 or stale:
                    options = []
                    for sha, files in variants.items():
                        row = files[0]
                        options.append({'sha256': sha, 'size': row['size'], 'copies': len(files),
                                        'valid': sha in valid,
                                        'error': info[row['id']].get('error', ''),
                                        'files': [{'id': r['id'], 'area': r['area'], 'rel': r['rel']}
                                                  for r in files]})
                    conflicts.append({'key': key, 'pkgid': p['pkgid'], 'variants': options,
                                      'selected': digest if policy['mode'] == 'select' and key not in unresolved else None,
                                      'unresolved': key in unresolved, 'stale': stale,
                                      'identical': len(variants) == 1, 'copies': len(rows)})
                for sha, files in variants.items():
                    keeps.extend(dict(r, sha=sha) for r in files if r in chosen)
            if not readable:
                missing.add(key + '（包损坏或元数据无效）')
                included.discard(key)
                continue
            selected[key] = chosen
            for row in chosen:
                group=by_main.get(row['id'])
                if group:
                    pending.extend(row_keys[int(fid)] for fid in group['members'] if int(fid) in row_keys)
            # Each chosen physical copy contributes its own dependency metadata.
            for row in chosen:
                if info[row['id']].get('error'):
                    continue
                for ref in info[row['id']].get('deps', []):
                    dep = resolve(ref)
                    if dep:
                        pending.append(dep['key'])
                    else:
                        missing.add(ref)
        return {'keys': sorted(included), 'missing': sorted(missing), 'selected': selected,
                'conflicts': sorted(conflicts, key=lambda c: c['key']),
                'unresolved': sorted(unresolved), 'keeps': keeps,
                'policy': {'mode': policy['mode'], 'choices': effective_choices}}
