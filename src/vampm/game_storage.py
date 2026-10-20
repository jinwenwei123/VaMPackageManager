"""Game-local bulk files with stable ownership and explicit legacy migration."""
import re
import uuid

from .file_moves import inventory, move_no_replace, recover_move
from .util import atomic_json, read_json, safe_path

FOLDER = '.PackageManagerStorage'
KINDS = ('imports', 'downloads', 'quarantine')
LABELS = {'imports': '导入暂存', 'downloads': '下载暂存', 'quarantine': '隔离区'}
CONFIG = 'game-storage.json'
JOURNAL = 'storage-migration.json'


def read_configuration(data):
    value = read_json(data / CONFIG)
    if (data / CONFIG).exists() and (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('owner'), str) or not re.fullmatch('[a-f0-9]{32}', value['owner'])
            or type(value.get('initialized')) is not bool
            or not isinstance(value.get('locations'), dict)
            or set(value['locations']) != set(KINDS)
            or any(v not in ('legacy', 'game') for v in value['locations'].values())):
        raise ValueError('游戏管理文件夹配置无效，已有文件保留。')
    return value


def game_base(game, owner):
    return safe_path(game, FOLDER + '/' + owner)


def check_binding(data, old_game, new_game):
    if (data / JOURNAL).exists():
        raise ValueError('游戏管理文件夹迁移尚未完成，请先继续迁移再更改绑定。')
    cfg = read_configuration(data)
    if not cfg or not cfg['initialized'] or old_game.resolve() == new_game.resolve(): return
    base = game_base(new_game, cfg['owner'])
    if not base.is_dir():
        raise ValueError('新位置缺少此目录的「.PackageManagerStorage」管理文件夹，请随游戏一并移动后再绑定；原数据保留。')
    if read_json(safe_path(base, 'owner.json')) != {'version': 1, 'owner': cfg['owner']}:
        raise ValueError('新位置的游戏管理文件夹归属不匹配，未更改绑定。')


class GameStorage:
    def __init__(self, catalog):
        self.c = catalog
        self.file = self.c.data / CONFIG
        self.journal = self.c.data / JOURNAL
        self.value = read_configuration(self.c.data)
        if self.value is None:
            locations = {}
            for kind in KINDS:
                old = safe_path(self.c.data, kind)
                if old.exists() and not old.is_dir(): raise ValueError('旧暂存或隔离路径不是文件夹。')
                locations[kind] = 'legacy' if old.is_dir() and any(old.iterdir()) else 'game'
            self._save({'version': 1, 'owner': uuid.uuid4().hex, 'initialized': False, 'locations': locations})
        if not self.value['initialized'] and self.game_available():
            self.ensure_base()

    @property
    def base(self):
        return game_base(self.c.game, self.value['owner'])

    def game_available(self):
        return self.c.game.is_dir() and (self.c.game / 'AddonPackages').is_dir()

    def _save(self, value):
        atomic_json(self.file, value)
        self.value = value

    def ensure_base(self):
        if not self.game_available(): raise ValueError('游戏目录当前不可用，未创建或移动管理文件。')
        base = self.base
        marker = safe_path(base, 'owner.json')
        expected = {'version': 1, 'owner': self.value['owner']}
        if base.exists():
            if not base.is_dir() or read_json(marker) != expected:
                raise ValueError('游戏管理文件夹已被其他内容占用或归属不匹配。')
        else:
            if self.value['initialized']:
                raise ValueError('游戏管理文件夹缺失，请恢复「.PackageManagerStorage」文件夹，已有索引保留。')
            base.mkdir(parents=True)
            atomic_json(marker, expected)
        if not self.value['initialized']: self._save({**self.value, 'initialized': True})
        return base

    def path(self, kind):
        if kind not in KINDS: raise ValueError('管理文件类型无效。')
        parent = self.c.data if self.value['locations'][kind] == 'legacy' else self.base
        return safe_path(parent, kind)

    def check_pending(self):
        if self.journal.exists():
            raise ValueError('游戏管理文件夹迁移待继续，请到「AI 与设置 → 游戏目录」继续迁移。')

    def ensure(self, kind):
        self.check_pending()
        if self.value['locations'][kind] == 'game': self.ensure_base()
        path = self.path(kind)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def public(self):
        return {'folder': str(self.base), 'metadata': str(self.c.data),
                'pending': self.journal.exists(),
                'legacy': [kind for kind in KINDS if self.value['locations'][kind] == 'legacy'],
                'paths': {kind: str(self.path(kind)) for kind in KINDS}}

    @staticmethod
    def _content(tree):
        return {'directory': tree['directory'], 'dirs': tree['dirs'],
                'files': {rel: version[:2] for rel, version in tree['files'].items()}}

    def migrate(self, job):
        self.ensure_base()
        migrated = []
        for kind in KINDS:
            pending = read_json(self.journal)
            if self.journal.exists() and (not isinstance(pending, dict) or pending.get('version') != 1
                                         or pending.get('kind') not in KINDS or 'tree' not in pending):
                raise ValueError('游戏管理文件迁移记录无效，未改动文件。')
            # Resume the one interrupted kind before processing any other kind.
            if pending and pending['kind'] != kind: continue
            if self.value['locations'][kind] == 'game' and not pending: continue
            job.check()
            source, target = safe_path(self.c.data, kind), safe_path(self.base, kind)
            if not pending:
                if target.exists(): raise ValueError('迁移目标已存在，未合并或覆盖：' + str(target))
                tree = inventory(source, job) if source.exists() else None
                pending = {'version': 1, 'kind': kind, 'tree': self._content(tree) if tree else None}
                atomic_json(self.journal, pending)
            job.message = '迁移' + LABELS[kind] + '；原文件校验成功后才移除'
            if pending['tree'] is not None:
                recover_move(source, target, job, finish=True)
                if source.exists(): move_no_replace(source, target, job)
                if not target.exists() or self._content(inventory(target, job)) != pending['tree']:
                    raise ValueError('迁移后的文件清单不匹配，迁移记录保留。')
            elif source.exists() or target.exists():
                raise ValueError('迁移期间文件夹状态发生变化，未自动处理。')
            self._save({**self.value, 'locations': {**self.value['locations'], kind: 'game'}})
            self.c.roots['quarantine'] = self.path('quarantine')
            self.journal.unlink()
            self.c.invalidate()
            migrated.append(kind)
        # If the pending kind came later, preceding legacy kinds still need migration.
        if any(v == 'legacy' for v in self.value['locations'].values()):
            migrated += self.migrate(job)['migrated']
        return {'migrated': migrated, 'storage': self.public()}
