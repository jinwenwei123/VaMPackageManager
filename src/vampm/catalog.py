import collections
import concurrent.futures
import json
import hashlib
import os
import re
import sqlite3
import stat
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

from .util import CATEGORIES, atomic_json, fingerprint, matches, read_json, safe_path, sha256, split_id, stamp
from .metadata import REVISION, load_metadata, representative_names
from .base_packages import BasePackages
from .bundles import Bundles
from .summary_state import SummaryState
from .content_tags import TagState, normalize_tags, TAG_ALIASES
from . import library_paths
from .game_storage import GameStorage

REFERENCE = re.compile(r'([^\s"<>|{}\[\],:/\\]+\.[^\s"<>|{}\[\],:/\\]+\.(?:\d+|latest|min\d+)):/', re.I)
CONTENT_REVISION = 1


def content_categories(counts):
    fields={'scene':'场景','appearance':'人物','clothing':'服饰','hair':'头发','morph':'变形',
            'texture':'皮肤纹理','script':'插件','asset':'环境道具','pose':'姿势动作'}
    found={category for field,category in fields.items() if counts.get(field)}
    return [c for c in CATEGORIES if c in found] or ['其他']


def package_categories(primary, detected):
    return list(dict.fromkeys([primary]+[c for c in detected if c!='其他'])) if primary!='其他' else list(detected)


def dependencies(meta):
    out = set()
    def walk(d):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if split_id(k):
                out.add(k)
            if isinstance(v, dict):
                walk(v.get('dependencies', {}))
    walk(meta.get('dependencies', {}))
    return out


def content_category(counts):
    """A dependency describes a relationship, not what a package contains."""
    if counts.get('scene'): return '人物' if counts.get('appearance') else '场景'
    for field,category in (('appearance','人物'),('clothing','服饰'),('hair','头发'),('pose','姿势动作'),
                           ('script','插件'),('asset','环境道具'),('morph','变形'),('texture','皮肤纹理')):
        if counts.get(field): return category
    return '其他'


def classify(names, package_name):
    lower = [n.lower() for n in names]
    counts = {
        'scene': sum(n.startswith('saves/scene/') and n.endswith('.json') for n in lower),
        'appearance': sum('/appearance/' in n and n.endswith('.vap') for n in lower),
        'clothing': sum(n.startswith('custom/clothing/') and n.endswith('.vam') or '/person/clothing/' in n and n.endswith('.vap') for n in lower),
        'hair': sum(n.startswith('custom/hair/') and n.endswith('.vam') or '/person/hair/' in n and n.endswith('.vap') for n in lower),
        'morph': sum('/morphs/' in n and n.endswith(('.vmi','.vap')) for n in lower),
        'texture': sum('/textures/' in n and n.endswith(('.png', '.jpg', '.jpeg')) or '/person/skin/' in n and n.endswith('.vap') for n in lower),
        'script': sum(n.endswith(('.cs', '.cslist')) or '/plugins/' in n and n.endswith('.vap') for n in lower),
        'asset': sum(n.endswith(('.assetbundle', '.scene', '.bundle')) for n in lower),
        'pose': sum('/pose/' in n and n.endswith('.vap') or n.endswith(('.vmd', '.bvh')) for n in lower),
    }
    # A scene may be only a character showcase. AI or a manual override can refine this.
    cat = content_category(counts)
    labels = {'scene':'场景','appearance':'外观预设','clothing':'服饰','hair':'发型','morph':'变形',
              'texture':'贴图','script':'脚本文件','asset':'资源文件','pose':'姿势或动作'}
    parts = [f'{v} 个{labels[k]}' for k,v in counts.items() if v]
    summary = '包含' + '、'.join(parts[:3]) + '。' if parts else '可供 VaM 加载的扩展资源，类型待确认。'
    return cat, summary, counts


def inspect_var(path, deep=False):
    result = {'deps': [], 'counts': {}, 'samples': [], 'description': '', 'url': '', 'license': '',
              'category': '其他', 'summary': '资源信息读取失败。', 'error': '', 'deep': int(deep),
              'metadata_revision':REVISION,'content_revision':CONTENT_REVISION,'metadata_warnings':[]}
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            names = [i.filename.replace('\\', '/') for i in infos]
            mi = next((i for i in infos if i.filename.lower() == 'meta.json'), None)
            if not mi or mi.file_size > 8 * 1024 * 1024:
                raise ValueError('缺少 meta.json 或元数据过大')
            meta,result['metadata_warnings'] = load_metadata(z.read(mi))
            result['description'] = str(meta.get('description', ''))[:4000]
            result['url'] = str(meta.get('promotionalLink', ''))[:1500]
            result['license'] = str(meta.get('licenseType', ''))[:200]
            dep = dependencies(meta)
            if deep:
                budget = 96 * 1024 * 1024
                skipped = 0
                for i in infos:
                    if i.filename.lower().endswith(('.json', '.vap', '.vaj', '.vmi', '.cs', '.cslist')) and i.filename.lower() != 'meta.json':
                        if i.file_size > 32 * 1024 * 1024 or i.file_size > budget:
                            skipped += 1
                            continue
                        budget -= i.file_size
                        content = z.read(i).decode('utf-8-sig', errors='replace')
                        dep.update(r for r in REFERENCE.findall(content) if split_id(r))
                result['deep'] = 1 if not skipped else 2
            cat, summary, counts = classify(names, Path(path).stem)
            result.update(category=cat, summary=summary, counts=counts,
                          deps=sorted(dep, key=str.casefold),
                          samples=representative_names(names,24))
    except Exception as e:
        result['error'] = str(e)[:500]
    return result


class Catalog:
    def __init__(self, game, home, data=None, create_active=True, disabled_folder=library_paths.DEFAULT_VAULT):
        self.game, self.home = Path(game).resolve(), Path(home).resolve()
        self.data = Path(data).resolve() if data is not None else self.home / 'data'
        self.data.mkdir(parents=True, exist_ok=True)
        self.roots = {'active': self.game / 'AddonPackages', 'vault': self.data / 'disabled',
                      'quarantine': self.data / 'quarantine'}
        if create_active: self.roots['active'].mkdir(exist_ok=True)
        self.dbpath = self.data / 'catalog.sqlite3'
        self._cache = None
        self._derived = {}
        self.revision = 0
        self.cache_lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY, area TEXT, rel TEXT, pkgkey TEXT, pkgid TEXT,
                    author TEXT, name TEXT, version TEXT, size INTEGER, mtime INTEGER,
                    info TEXT, sha TEXT DEFAULT '', sha_mtime INTEGER DEFAULT 0,
                    UNIQUE(area,rel));
                CREATE INDEX IF NOT EXISTS idx_package ON files(pkgkey);
                CREATE TABLE IF NOT EXISTS labels (
                    pkgkey TEXT PRIMARY KEY, category TEXT, summary TEXT, source TEXT,
                    favorite INTEGER DEFAULT 0, updated TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS favorite_origins (file_id INTEGER PRIMARY KEY, original_rel TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS file_warnings (
                    file_id INTEGER PRIMARY KEY, size INTEGER NOT NULL, mtime INTEGER NOT NULL, messages TEXT NOT NULL);
            ''')
            # New libraries park unused VARs beside AddonPackages. Preserve the
            # location of an existing legacy vault instead of silently orphaning it.
            db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',
                       ('disabled_location', 'legacy' if (self.data/'disabled').exists() else 'game'))
            db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',
                       ('disabled_folder', library_paths.folder_name(disabled_folder)))
            SummaryState.setup(db)
            TagState.setup(db)
        library_paths.recover(self)
        self.roots['vault'] = library_paths.vault_path(self.game, self.data, library_paths.configuration(self.data))
        self.storage = GameStorage(self)
        self.roots['quarantine'] = self.storage.path('quarantine')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def invalidate(self, packages=True):
        with self.cache_lock:
            if packages:
                self._cache = None
            self._derived.clear()
            self.revision += 1

    def path(self, row):
        return safe_path(self.roots[row['area']], row['rel'])

    def _memo(self, key, factory):
        with self.cache_lock:
            if key not in self._derived:
                self._derived[key] = factory()
            return self._derived[key]

    def walk(self, area, errors=None):
        """DirEntry reuses Windows directory enumeration metadata (one stat, no Path probes)."""
        root = self.roots[area]
        if not root.exists(): return
        pending = [(str(root), '')]
        while pending:
            folder, prefix = pending.pop()
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        rel = prefix + entry.name
                        try:
                            if entry.is_symlink(): continue
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if not is_dir and not entry.name.lower().endswith('.var'): continue
                            s = entry.stat(follow_symlinks=False)
                            if getattr(s, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT: continue
                            if is_dir:
                                pending.append((entry.path, rel + '/'))
                            elif stat.S_ISREG(s.st_mode):
                                yield Path(entry.path), rel, s.st_size, s.st_mtime_ns
                        except OSError as e:
                            if errors is not None: errors.append({'path':rel, 'error':str(e)})
            except OSError as e:
                if errors is not None: errors.append({'path':folder, 'error':str(e)})

    def scan(self, job, deep=False):
        with self.db() as db:
            old = {(r['area'], r['rel']): dict(r) for r in db.execute(
                    "SELECT id,area,rel,size,mtime,info,json_extract(info,'$.content_revision') AS content_rev,json_extract(info,'$.deep') AS deep, json_extract(info,'$.error') AS error, json_extract(info,'$.metadata_revision') AS parser FROM files WHERE area IN ('active','vault')")}
        inventory, errors, seen = [], [], set()
        job.update(0, 0, '枚举文件变动')
        for area in ('active','vault'):
            for path, rel, size, mt in self.walk(area, errors):
                job.check()
                seen.add((area, rel))
                prev = old.get((area, rel))
                if (not prev or prev['size'] != size or prev['mtime'] != mt
                        or (deep and not prev['deep']) or (prev['error'] and prev['parser']!=REVISION)
                        or prev['content_rev']!=CONTENT_REVISION):
                    inventory.append((area, rel, path, size, mt))
                if len(seen) % 500 == 0:
                    job.update(len(seen), 0, f'已检查 {len(seen)} 个文件，发现 {len(inventory)} 个需要读取')
        job.update(0, len(inventory), '读取 VAR 元数据与文件目录')
        changed = 0
        favorite_changes=0
        favorite_root=self.favorite_folder()
        def inspect(item):
            job.check()
            area, rel, path, size, mt = item
            prev=old.get((area,rel),{})
            counts_only=(prev.get('size')==size and prev.get('mtime')==mt and not prev.get('error')
                         and prev.get('content_rev')!=CONTENT_REVISION and (not deep or prev.get('deep')))
            info = inspect_var(path, False if counts_only else deep or bool(prev.get('deep')))
            if counts_only and not info['error']:
                # Upgrade file-type counts using ZIP directories; retain already
                # scanned dependencies instead of rereading every scene/script.
                info={**json.loads(prev['info']),**{k:info[k] for k in ('category','counts','summary','samples','content_revision')}}
            if fingerprint(path) != [size, mt]: raise ValueError('扫描期间文件发生变化，请重新扫描：' + rel)
            return item, info
        # Bounded batches: unchanged packages create no reader jobs and no metadata reads.
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool, self.db() as db:
            for start in range(0, len(inventory), 128):
                for item, info in pool.map(inspect, inventory[start:start+128]):
                    job.check()
                    area, rel, path, size, mt = item
                    self._upsert(db, area, rel, size, mt, info)
                    changed += 1
                    job.update(changed, len(inventory), path.name)
            # Never drop rows for a temporarily unreadable file.
            for key, row in old.items():
                if key not in seen and not errors and not self.path(row).exists():
                    db.execute('DELETE FROM files WHERE id=?', (row['id'],))
                    changed += 1
            # The designated collection folder is also an import destination: a
            # manually added VAR becomes a favorite on the next index refresh.
            favored={r['pkgkey'] for r in db.execute("SELECT pkgkey,rel FROM files WHERE area IN ('active','vault')")
                     if self.in_folder(r['rel'],favorite_root)}
            already_favored={r['pkgkey'] for r in db.execute('SELECT pkgkey FROM labels WHERE favorite=1')}
            for key in favored-already_favored:
                db.execute('''INSERT INTO labels(pkgkey,favorite,updated) VALUES(?,1,?) ON CONFLICT(pkgkey)
                    DO UPDATE SET favorite=1,updated=excluded.updated''',(key,stamp()))
                favorite_changes+=1
            db.execute('DELETE FROM favorite_origins WHERE file_id NOT IN (SELECT id FROM files)')
            db.execute('DELETE FROM file_warnings WHERE file_id NOT IN (SELECT id FROM files)')
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('last_scan', stamp()))
        self.invalidate(packages=bool(changed or favorite_changes))
        job.update(len(seen), len(seen), f'完成：{len(seen)} 个文件，更新 {changed} 个')
        return {'files': len(seen), 'updated': changed, 'errors': errors, 'deep': deep}

    @staticmethod
    def _upsert(db, area, rel, size, mt, info, digest=''):
        pid = rel.rsplit('/',1)[-1][:-4]
        parsed = split_id(pid)
        author, name, version = parsed or ('未知', pid, '')
        if not parsed or not version.isdigit():
            info = dict(info, error=(info['error'] + '；文件名不符合 作者.包名.版本.var').strip('；'))
        db.execute('''INSERT INTO files(area,rel,pkgkey,pkgid,author,name,version,size,mtime,info,sha,sha_mtime)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(area,rel) DO UPDATE SET
            pkgkey=excluded.pkgkey,pkgid=excluded.pkgid,author=excluded.author,name=excluded.name,
            version=excluded.version,size=excluded.size,mtime=excluded.mtime,info=excluded.info,
            sha=excluded.sha,sha_mtime=excluded.sha_mtime''',
            (area,rel,pid.casefold(),pid,author,name,version,size,mt,json.dumps(info,ensure_ascii=False),digest,mt if digest else 0))
        if area in ('active','vault'):SummaryState.observe(db,pid.casefold(),size,mt)
        if area in ('active','vault'):TagState.observe(db,pid.casefold(),size,mt)

    def index_files(self, entries):
        """Install only the metadata already validated by import/download; no library rescan."""
        if not entries: return
        with self.db() as db:
            for entry in entries:
                area, rel = entry['area'], entry['rel']
                size, mt = fingerprint(safe_path(self.roots[area], rel))
                self._upsert(db, area, rel, size, mt, entry['info'], entry.get('sha',''))
                if entry.get('warnings'):
                    fid=db.execute('SELECT id FROM files WHERE area=? AND rel=?',(area,rel)).fetchone()[0]
                    db.execute('INSERT OR REPLACE INTO file_warnings VALUES(?,?,?,?)',
                               (fid,size,mt,json.dumps(entry['warnings'],ensure_ascii=False)))
        self.invalidate()

    def relocate(self, moves, reverse=False):
        """Reconcile touched paths after a move/recovery, retaining deep scans and hashes.

        Quarantined rows are retained privately for undo but excluded from the package library.
        A crash or an older operation may have no cached row: read only that restored VAR.
        """
        if not moves: return
        before, after = ('to','from') if reverse else ('from','to')
        with self.db() as db:
            for m in moves:
                if m.get('auxiliary'):continue
                area, rel = m[after+'_area'], m[after+'_rel']
                path = safe_path(self.roots[area], rel)
                if not path.exists(): continue
                if fingerprint(path) != [m['size'],m['mtime']]:
                    raise ValueError('索引更新时文件发生变化：' + rel)
                existing = db.execute('SELECT size,mtime FROM files WHERE area=? AND rel=?',(area,rel)).fetchone()
                if existing and [existing['size'],existing['mtime']] == [m['size'],m['mtime']]: continue
                row = db.execute('SELECT id FROM files WHERE area=? AND rel=? AND size=? AND mtime=?',
                    (m[before+'_area'],m[before+'_rel'],m['size'],m['mtime'])).fetchone()
                if existing: db.execute('DELETE FROM files WHERE area=? AND rel=?',(area,rel))
                if row:
                    db.execute('UPDATE files SET area=?,rel=? WHERE id=?',(area,rel,row['id']))
                else:
                    self._upsert(db,area,rel,m['size'],m['mtime'],inspect_var(path,deep=True))
        self.invalidate()

    def rows(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM files WHERE area IN ('active','vault') ORDER BY id")]

    def signature(self):
        with self.db() as db:
            rows=[tuple(r) for r in db.execute("SELECT area,rel,size,mtime FROM files WHERE area IN ('active','vault') ORDER BY area,rel")]
        return hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()

    def canonical_keys(self,keys):
        """Management aliases preserve saved selections after filename repair.

        Dependency resolution deliberately uses real VAR identities, not aliases.
        """
        aliases=read_json(self.data/'package-key-aliases.json',{})
        result=[];added=set()
        for key in keys:
            key=str(key).casefold();seen=set()
            while key in aliases and key not in seen:
                seen.add(key);key=aliases[key]
            if key not in added:result.append(key);added.add(key)
        return result

    def packages(self):
        with self.cache_lock:
            if self._cache is not None: return self._cache
            with self.db() as db:
                labels = {r['pkgkey']: dict(r) for r in db.execute('SELECT * FROM labels')}
                summary_states={r['pkgkey']:dict(r) for r in db.execute('SELECT * FROM summary_state')}
                tag_states={r['pkgkey']:dict(r) for r in db.execute('SELECT * FROM package_tags')}
                warnings = {r['file_id']:dict(r) for r in db.execute('SELECT * FROM file_warnings')}
            bykey = collections.defaultdict(list)
            info_by_id = {}
            for r in self.rows():
                bykey[r['pkgkey']].append(r)
                info_by_id[r['id']] = json.loads(r['info'])
            result = {}
            for k, rows in bykey.items():
                rows.sort(key=lambda r: (bool(info_by_id[r['id']].get('error')), r['area']!='active',len(r['rel']),r['rel'].casefold()))
                r = rows[0]
                info = info_by_id[r['id']]
                label = labels.get(k, {})
                category=label.get('category') or info['category']
                if category not in CATEGORIES:
                    # Read old "dependency" labels through the existing content
                    # index. Preserve summaries, favorites and the original rows.
                    category=info['category'] if info['category'] in CATEGORIES else content_category(info.get('counts',{}))
                alerts=list(info.get('metadata_warnings',[]))
                detected=set()
                for f in rows:
                    if not info_by_id[f['id']].get('error'):
                        detected.update(content_categories(info_by_id[f['id']].get('counts',{})))
                detected=[c for c in CATEGORIES if c in detected and c!='其他'] or ['其他']
                for f in rows:
                    notice=warnings.get(f['id'])
                    if notice and (notice['size'],notice['mtime'])==(f['size'],f['mtime']):
                        alerts.extend(json.loads(notice['messages']))
                result[k] = dict(key=k, id=r['id'], pkgid=r['pkgid'], name=r['name'], author=r['author'],
                    version=r['version'], size=r['size'], mtime=r['mtime'], area=r['area'],
                    active=any(f['area']=='active' for f in rows), copies=len(rows),
                    category=category, categories=package_categories(category,detected),content_categories=detected,
                    summary=label.get('summary') or info['summary'],
                    source=label.get('source') or 'rules', favorite=bool(label.get('favorite')),
                    summary_generated=SummaryState.complete(summary_states.get(k),rows,label.get('summary')),
                    summary_run_id=summary_states.get(k,{}).get('run_id',''),
                    tags=json.loads(tag_states.get(k,{}).get('tags','[]')),
                    tags_generated=TagState.complete(tag_states.get(k),rows),
                    tags_run_id=tag_states.get(k,{}).get('run_id',''),
                    description=info['description'], url=info['url'], license=info['license'],
                    deps=info['deps'], counts=info['counts'], samples=info['samples'],
                    deep=info.get('deep',0), error=info['error'], metadata_warnings=info.get('metadata_warnings',[]),
                    warnings=list(dict.fromkeys(alerts)),files=rows)
            BasePackages(self).annotate(result,[r for rows in bykey.values() for r in rows])
            Bundles(self).annotate(result)
            self._cache = result
            return result

    def resolver(self):
        return self._memo('resolver', self._resolver)

    def _resolver(self):
        pkgs = self.packages()
        groups = collections.defaultdict(list)
        for p in pkgs.values():
            if p['version'].isdigit() and not p['error']:
                groups[p['key'].rsplit('.',1)[0]].append(p)
        for v in groups.values(): v.sort(key=lambda p:int(p['version']), reverse=True)
        resolved = {}
        def resolve(ref):
            key = ref.casefold()
            if key in resolved: return resolved[key]
            part = split_id(ref)
            if not part: return None
            result = next((p for p in groups.get(key.rsplit('.',1)[0],[]) if matches(ref,p['pkgid'])), None)
            resolved[key] = result
            return result
        return resolve

    def missing(self, keys=None):
        if keys: return self._missing(keys)
        return self._memo('missing', self._missing)

    def _missing(self, keys=None):
        pkgs, resolve = self.packages(), self.resolver()
        found = {}
        source = self.closure(keys)['keys'] if keys else list(pkgs)
        for key in source:
            p = pkgs.get(key)
            if not p: continue
            for ref in p['deps']:
                if resolve(ref): continue
                item = found.setdefault(ref.casefold(), {'ref': ref, 'users': [], 'count': 0})
                item['count'] += 1
                if len(item['users']) < 12: item['users'].append(p['pkgid'])
        return sorted(found.values(), key=lambda d:(-d['count'],d['ref'].casefold()))

    def closure(self, keys):
        pkgs, resolve = self.packages(), self.resolver()
        pending = [str(k).casefold() for k in keys]
        chosen, missing = set(), set()
        while pending:
            key = pending.pop()
            if key in chosen: continue
            p = pkgs.get(key)
            if not p or p['error']:
                missing.add(key + ('（包损坏或元数据无效）' if p else ''))
                continue
            chosen.add(key)
            for ref in p['deps']:
                dep = resolve(ref)
                if dep: pending.append(dep['key'])
                else: missing.add(ref)
        return {'keys': sorted(chosen), 'missing': sorted(missing)}

    def _filtered(self, search, cat, mode, sort, scope=''):
        rows = list(self.packages().values())
        if scope=='favorites': rows=[p for p in rows if p['favorite']]
        if search:
            words = search.split()
            terms = self._memo('search_text', lambda: {
                p['key']:(p['pkgid']+' '+p['summary']+' '+' '.join(p.get('tags',[])+p['categories'])+' '+p['files'][0]['rel']).casefold()
                for p in self.packages().values()})
            rows = [p for p in rows if all(w in terms[p['key']] or TAG_ALIASES.get(w,w).casefold() in terms[p['key']] for w in words)]
        if cat: rows = [p for p in rows if cat in p['categories']]
        if mode == 'active': rows = [p for p in rows if p['active']]
        if mode == 'pinned': rows = [p for p in rows if p['pinned']]
        if mode == 'disabled': rows = [p for p in rows if not p['active']]
        if mode == 'favorite': rows = [p for p in rows if p['favorite']]
        if mode == 'errors': rows = [p for p in rows if p['error']]
        if mode == 'warnings': rows = [p for p in rows if p.get('warnings')]
        if mode == 'unlabeled': rows = [p for p in rows if not p['summary_generated']]
        rows.sort(key=(lambda p:-p['mtime']) if sort=='new' else (lambda p:-p['size']) if sort=='size' else (lambda p:p['key']))
        return rows

    def list(self, query):
        scope=query.get('scope','')
        if scope not in ('','favorites'):raise ValueError('无效的资源范围')
        key = ('query', str(query.get('q','')).casefold().strip(), query.get('category',''),
               query.get('state',''), query.get('sort','name'),scope)
        with self.cache_lock:
            cache = self._derived.setdefault('queries', collections.OrderedDict())
            def cached(k):
                if k not in cache:
                    cache[k] = {'rows':self._filtered(*k[1:])}
                    if len(cache) > 32: cache.popitem(last=False)
                cache.move_to_end(k)
                return cache[k]
            result=cached(key);rows=result['rows']
            if query.get('keys_only'):
                return {'keys':[p['key'] for p in rows]}
            limit = min(100,max(1,int(query.get('limit',40))))
            page = min(max(1,int(query.get('page',1))),max(1,(len(rows)+limit-1)//limit))
            out={'total':len(rows),'page':page,'items':[self.public(p) for p in rows[(page-1)*limit:page*limit]]}
            if query.get('with_counts') in (True,'1','true'):
                if 'summary' not in result:
                    count=sum(p['copies'] for p in rows)
                    result['summary']={'packages':len(rows),'files':count,'duplicates':count-len(rows),
                        'bytes':sum(f['size'] for p in rows for f in p['files']),
                        'active':sum(p['active'] for p in rows),'errors':sum(bool(p['error']) for p in rows)}
                # Category tabs describe this search + scope + status before
                # category filtering, so other categories remain discoverable.
                base=cached((key[0],key[1],'',*key[3:]))
                if 'facets' not in base:
                    base['facets']={'total':len(base['rows']),
                        'categories':dict(collections.Counter(c for p in base['rows'] for c in p['categories']))}
                out.update(summary=result['summary'],facets=base['facets'])
            return out

    @staticmethod
    def public(p):
        return {k:v for k,v in p.items() if k not in ('files','description','deps','samples')}

    def detail(self, key):
        p = self.packages().get(key.casefold())
        if not p: raise ValueError('包不存在，请重新扫描')
        resolve = self.resolver()
        out = {k:v for k,v in p.items() if k!='files'}
        out['files'] = [{'id':r['id'],'area':r['area'],'rel':r['rel'],'size':r['size']} for r in p['files']]
        out['dependencies'] = [{'ref':ref, 'resolved':r['pkgid'] if (r:=resolve(ref)) else None,
                                'active': bool(r and r['active'])} for ref in p['deps']]
        return out

    def stats(self):
        return self._memo('stats', self._stats)

    def _stats(self):
        pkgs = self.packages()
        with self.db() as db:
            last = db.execute("SELECT value FROM settings WHERE key='last_scan'").fetchone()
            count,size=db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE area IN ('active','vault')").fetchone()
        return {'packages':len(pkgs),'files':count,'bytes':size,
                'pinned':sum(p['pinned'] for p in pkgs.values()),
                'pin_roots':sum(p['pin_root'] for p in pkgs.values()),
                'active':sum(p['active'] for p in pkgs.values()),
                'duplicates':count-len(pkgs),'errors':sum(bool(p['error']) for p in pkgs.values()),
                'favorites':sum(p['favorite'] for p in pkgs.values()),'favorite_folder':self.favorite_folder(),
                'warnings':sum(bool(p.get('warnings')) for p in pkgs.values()),
                'categories':dict(collections.Counter(c for p in pkgs.values() for c in p['categories'])),
                'last_scan':last[0] if last else None,
                'game':str(self.game),'disabled_folder':str(self.roots['vault']),'categories_all':CATEGORIES,
                'deep':sum(bool(p['deep']) for p in pkgs.values())}

    def summary_run_keys(self,run_id,target='summary'):
        if not run_id:return set()
        with self.db() as db:
            tables={'summary':'summary_state','tags':'package_tags','both':'summary_state'}
            keys={r['pkgkey'] for r in db.execute(f"SELECT pkgkey FROM {tables[target]} WHERE run_id=?",(run_id,))}
            if target=='both':keys|={r['pkgkey'] for r in db.execute('SELECT pkgkey FROM package_tags WHERE run_id=?',(run_id,))}
            return keys

    def label(self, key, category=None, summary=None, source='manual', favorite=None, summary_run_id=None, tags=None):
        key = key.casefold()
        if tags is not None: tags=normalize_tags(tags)
        if category is not None and category not in CATEGORIES: raise ValueError('分类无效')
        with self.db() as db:
            if not db.execute("SELECT 1 FROM files WHERE pkgkey=? AND area IN ('active','vault') LIMIT 1",(key,)).fetchone():
                raise ValueError('包不存在')
            old = db.execute('SELECT * FROM labels WHERE pkgkey=?',(key,)).fetchone()
            old = dict(old) if old else {}
            db.execute('INSERT OR REPLACE INTO labels VALUES(?,?,?,?,?,?)',
                       (key,category if category is not None else old.get('category'),
                        str(summary) if summary is not None else old.get('summary'),
                        source if summary is not None or category is not None else old.get('source'),
                        int(favorite) if favorite is not None else old.get('favorite',0),stamp()))
            if summary is not None:
                summary_state=SummaryState.save(db,key,summary,source,summary_run_id)
            if tags is not None: tag_state=TagState.save(db,key,tags,source,summary_run_id)
        with self.cache_lock:
            if self._cache is not None and key in self._cache:
                p = dict(self._cache[key])
                if category is not None:
                    p['category'] = category
                    p['categories']=package_categories(category,p['content_categories'])
                if summary is not None:
                    p['summary'] = str(summary)
                    p['summary_generated']=SummaryState.complete(summary_state,p['files'],summary)
                    p['summary_run_id']=summary_state['run_id']
                if tags is not None:
                    p.update(tags=tags,tags_generated=TagState.complete(tag_state,p['files']),tags_run_id=tag_state['run_id'])
                if category is not None or summary is not None: p['source'] = source
                if favorite is not None: p['favorite'] = bool(favorite)
                self._cache[key] = p
            self.invalidate(packages=False)

    def normalize_folder(self, folder):
        value=str(folder or '').strip().replace('\\','/').rstrip('/')
        root=str(self.roots['active']).replace('\\','/')
        if value.casefold().startswith(root.casefold()+'/'):value=value[len(root)+1:]
        if not value or value=='.':raise ValueError('请选择 AddonPackages 内的具体文件夹')
        safe_path(self.roots['active'],value)
        return value

    @staticmethod
    def in_folder(rel,folder):
        return rel.casefold().startswith(folder.rstrip('/').casefold()+'/')

    def favorite_folder(self):
        with self.db() as db:
            row=db.execute("SELECT value FROM settings WHERE key='favorite_folder'").fetchone()
        return row[0] if row else '收藏'

    def favorite_keys(self):return [k for k,p in self.packages().items() if p['favorite']]

    def folder_keys(self,folder):
        folder=self.normalize_folder(folder)
        return [k for k,p in self.packages().items() if any(self.in_folder(f['rel'],folder) for f in p['files'])]

    def folders(self):
        def build():
            byfolder=collections.defaultdict(set)
            for key,p in self.packages().items():
                for f in p['files']:
                    parts=f['rel'].split('/')[:-1]
                    for n in range(1,len(parts)+1):byfolder['/'.join(parts[:n])].add(key)
            return [{'path':path,'count':len(keys)} for path,keys in sorted(byfolder.items(),key=lambda x:x[0].casefold())]
        return self._memo('folders',build)

    def favorite_origins(self):
        with self.db() as db:return {str(r['file_id']):r['original_rel'] for r in db.execute('SELECT * FROM favorite_origins')}

    def rename_collection_index(self,old,new):
        """Retarget collection paths without rescanning or changing file IDs."""
        def renamed(value):
            if value.casefold()==old.casefold():return new
            return new+value[len(old):] if self.in_folder(value,old) else value
        profiles_path=self.data/'profiles.json'
        profiles=read_json(profiles_path,[]);changed=False
        for p in profiles:
            source=p.get('source',{})
            if source.get('type')=='folder':
                value=renamed(source['folder'])
                if value!=source['folder']:source['folder']=value;changed=True
        if changed:atomic_json(profiles_path,profiles)
        with self.db() as db:
            for r in db.execute("SELECT id,rel FROM files WHERE area IN ('active','vault')").fetchall():
                value=renamed(r['rel'])
                if value!=r['rel']:db.execute('UPDATE files SET rel=? WHERE id=?',(value,r['id']))
            for r in db.execute('SELECT file_id,original_rel FROM favorite_origins').fetchall():
                value=renamed(r['original_rel'])
                if value!=r['original_rel']:db.execute('UPDATE favorite_origins SET original_rel=? WHERE file_id=?',(value,r['file_id']))
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('favorite_folder',new))
        self.invalidate()

    def apply_favorite_metadata(self,plan,reverse=False):
        changes=plan.get('favorite_changes',{})
        if not changes:return
        side='before' if reverse else 'after'
        with self.db() as db:
            for key,value in changes.items():
                db.execute('''INSERT INTO labels(pkgkey,favorite,updated) VALUES(?,?,?) ON CONFLICT(pkgkey)
                    DO UPDATE SET favorite=excluded.favorite,updated=excluded.updated''',(key,int(value[side]),stamp()))
            for fid,value in plan.get('origin_changes',{}).items():
                if value[side] is None:db.execute('DELETE FROM favorite_origins WHERE file_id=?',(int(fid),))
                else:db.execute('INSERT OR REPLACE INTO favorite_origins VALUES(?,?)',(int(fid),value[side]))
            if plan.get('favorite_root'):
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('favorite_folder',plan['favorite_root'][side]))
        self.invalidate()

    def duplicate_audit(self, job):
        candidates = [p for p in self.packages().values() if p['copies']>1]
        total = sum(p['copies'] for p in candidates)
        groups, conflicts, done = [], [], 0
        for p in candidates:
            byhash = collections.defaultdict(list)
            for r in p['files']:
                job.check()
                path = self.path(r)
                before = fingerprint(path)
                if before != [r['size'],r['mtime']]: raise ValueError('文件已变动，请重新扫描：'+r['rel'])
                h = r['sha'] if r['sha'] and r['sha_mtime']==r['mtime'] else sha256(path,job)
                if fingerprint(path)!=before: raise ValueError('校验期间文件发生变化：'+r['rel'])
                byhash[h].append({'id':r['id'],'area':r['area'],'rel':r['rel'],'size':r['size'],'mtime':r['mtime'],'sha':h})
                with self.db() as db:
                    db.execute('UPDATE files SET sha=?,sha_mtime=? WHERE id=?',(h,r['mtime'],r['id']))
                done += 1
                job.update(done,total,p['pkgid'])
            if len(byhash)>1:
                conflicts.append({'pkgid':p['pkgid'],'variants':list(byhash.values())})
            for h, files in byhash.items():
                if len(files)>1:
                    files.sort(key=lambda f:(f['area']!='active',len(f['rel']),f['rel'].casefold()))
                    groups.append({'pkgid':p['pkgid'],'sha':h,'keep':files[0], 'extras':files[1:],
                                   'bytes':sum(f['size'] for f in files[1:]),'conflict':len(byhash)>1})
        result = {'created':stamp(),'signature':self.signature(),'groups':groups,'conflicts':conflicts,
                  'extras':sum(len(g['extras']) for g in groups),'bytes':sum(g['bytes'] for g in groups)}
        atomic_json(self.data/'duplicate-audit.json',result)
        self.invalidate()
        return {'groups':len(groups),'conflicts':len(conflicts),'extras':result['extras'],'bytes':result['bytes']}
