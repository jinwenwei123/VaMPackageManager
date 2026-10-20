"""Stable directory identities; legacy data stays in place, new libraries stay isolated."""
import os
import re
import sqlite3
import uuid
from pathlib import Path

from .util import atomic_json, read_json
from . import library_paths, game_storage


class WorkspaceChanged(ValueError):
    pass


class GameDirectories:
    def __init__(self, home, game):
        self.data = Path(home).resolve() / 'data'
        self.file = self.data / 'game-directories.json'
        self.created = not self.file.exists()
        if self.created:
            self.value = {'version': 1, 'current': None, 'items': []}
            if game is not None:
                path = str(Path(game).resolve())
                self.value.update(current='original', items=[
                    {'id': 'original', 'name': Path(path).name or '原游戏目录', 'path': path}])
            self._save(self.value)
        else:
            self.value = read_json(self.file)
            value = self.value
            if (not isinstance(value, dict) or value.get('version') != 1
                    or not isinstance(value.get('items'), list)):
                raise ValueError('游戏目录登记文件无效；原数据保留，请恢复 data/game-directories.json。')
            ids, paths = set(), set()
            for item in value['items']:
                if (not isinstance(item, dict) or not self.valid_id(item.get('id'))
                        or not isinstance(item.get('path'), str) or not Path(item['path']).is_absolute()
                        or type(item.get('archived', False)) is not bool
                        or not isinstance(item.get('name'), str) or not item['name']):
                    raise ValueError('游戏目录登记项无效，未改动已有数据。')
                key = self.path_key(item['path'])
                library_paths.folder_name(item.get('disabled_folder', library_paths.DEFAULT_VAULT))
                if item['id'] in ids or (not item.get('archived') and key in paths):
                    raise ValueError('游戏目录登记重复，未改动已有数据。')
                ids.add(item['id'])
                if not item.get('archived'): paths.add(key)
            current = value.get('current')
            if ('current' not in value or (ids and 'original' not in ids) or (current is not None and current not in ids)
                    or any(p['id'] == current and p.get('archived') for p in value['items'])):
                raise ValueError('当前游戏目录登记无效，未改动已有数据。')

    @staticmethod
    def valid_id(value):
        return isinstance(value, str) and (value == 'original' or bool(re.fullmatch(r'[a-f0-9]{32}', value)))

    @staticmethod
    def path_key(value):
        return os.path.normcase(str(Path(value).resolve()))

    def entry(self, ident=None):
        ident = ident or self.value['current']
        item = next((p for p in self.value['items'] if p['id'] == ident), None)
        if not item: raise ValueError('游戏目录已不存在，请刷新列表。')
        return dict(item)

    def location(self, ident):
        self.entry(ident)
        return self.data if ident == 'original' else self.data / 'workspaces' / ident

    def _save(self, value):
        atomic_json(self.file, value)
        self.value = value

    def public(self):
        items, archived = [], []
        for p in self.value['items']:
            data = self.location(p['id'])
            row = {**p, **library_paths.public_paths(Path(p['path']), data,
                          p.get('disabled_folder', library_paths.DEFAULT_VAULT)), 'data_path': str(data)}
            (archived if p.get('archived') else items).append(row)
        return {'current': self.value['current'], 'items': items, 'archived': archived}

    @staticmethod
    def available(item):
        path = Path(item['path'])
        return not item.get('archived') and path.is_dir() and (path / 'AddonPackages').is_dir()

    @classmethod
    def game_path(cls, path):
        if not isinstance(path, str) or not path.strip(): raise ValueError('请输入 VaM 游戏根目录。')
        path = Path(path.strip().strip('"')).expanduser()
        if not path.is_absolute(): raise ValueError('请使用完整的绝对路径。')
        path = path.resolve(strict=True)
        if not cls.available({'path': str(path)}): raise ValueError('请选择包含 AddonPackages 文件夹的 VaM 游戏根目录。')
        return path

    def register(self, path, name='', disabled_folder=library_paths.DEFAULT_VAULT):
        disabled_folder = library_paths.folder_name(disabled_folder)
        path = self.game_path(path)
        matches = [p for p in self.value['items'] if self.path_key(p['path']) == self.path_key(path)]
        if any(not p.get('archived') for p in matches):
            raise ValueError('此游戏目录已保存，无需重复添加。')
        if len(matches) > 1:
            raise ValueError('此路径有多份保留的数据，请从「已移除目录的数据」中选择要恢复的一份。')
        if matches:
            item = self.prepare_binding(matches[0]['id'], str(path), name or path.name)
            self.save_binding(item)
            return self.public()
        self.check_location(path, disabled_folder=disabled_folder)
        item = {'id': uuid.uuid4().hex if self.value['items'] else 'original',
                'name': self.unique_name(name or path.name), 'path': str(path),
                'disabled_folder': disabled_folder}
        self._save({**self.value, 'items': [*self.value['items'], item]})
        return self.public()

    def prepare_binding(self, ident, path, name=None):
        if not self.valid_id(ident): raise ValueError('请选择要重新绑定的目录数据。')
        item = self.entry(ident)
        path = self.game_path(path)
        if any(p['id'] != ident and not p.get('archived') and self.path_key(p['path']) == self.path_key(path)
               for p in self.value['items']):
            raise ValueError('此路径已绑定另一份目录数据；请先移除那个目录（保留其数据），再绑定所选数据。')
        data = self.location(ident)
        if (data / 'library-path-change.json').exists():
            raise ValueError('此目录有待恢复的停用库路径变更，请先恢复后再重新绑定。')
        if (data / game_storage.JOURNAL).exists():
            raise ValueError('此目录有待继续的管理文件迁移，请先完成迁移后再重新绑定。')
        self.check_location(path, ident)
        config = library_paths.configuration(data, item.get('disabled_folder', library_paths.DEFAULT_VAULT))
        old_game = Path(item['path'])
        old_roots = [(old_game / 'AddonPackages').resolve(), library_paths.vault_path(old_game, data, config)]
        if any(path == root or root in path.parents for root in old_roots):
            raise ValueError('不能将原启用库或停用库内部的目录绑定为游戏根目录。')
        if (self.path_key(path) != self.path_key(item['path']) and config['mode'] == 'game'
                and not library_paths.vault_path(path, data, config).is_dir()):
            # An indexed inactive library must move with the game, rather than disappear
            # silently from the next scan. Empty/unopened libraries do not require a vault.
            dbpath = data / 'catalog.sqlite3'
            if dbpath.exists():
                db = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)
                try:
                    has_vault = db.execute("SELECT 1 FROM files WHERE area='vault' LIMIT 1").fetchone()
                finally: db.close()
                if has_vault:
                    raise ValueError(f'新目录缺少停用文件夹「{config["folder"]}」，请将停用资源一并移动后再绑定；原数据保留。')
        game_storage.check_binding(data, old_game, path)
        return {**item, 'path': str(path), 'archived': False,
                'name': self.unique_name(name if name is not None else item['name'], ident)}

    def save_binding(self, item):
        item = {**item, 'name': self.unique_name(item['name'], item['id'])}
        self._save({**self.value, 'items': [item if p['id'] == item['id'] else p for p in self.value['items']]})

    def archive(self, ident):
        if not self.valid_id(ident): raise ValueError('请选择要移除的游戏目录。')
        self.entry(ident)
        self._save({**self.value,
                    'current': None if self.value['current'] == ident else self.value['current'],
                    'items': [{**p, 'archived': True} if p['id'] == ident else p for p in self.value['items']]})

    def check_location(self, path, ident=None, disabled_folder=None):
        # Resolve library roots too: different game paths may share a symlinked package library.
        protected = [self.data.resolve()]
        protected.extend((Path(item['path']) / game_storage.FOLDER).resolve()
                         for item in self.value['items'] if not item.get('archived'))
        for item in self.value['items']:
            if item['id'] != ident and not item.get('archived'):
                game, data = Path(item['path']), self.location(item['id'])
                config = library_paths.configuration(data, item.get('disabled_folder', library_paths.DEFAULT_VAULT))
                protected.extend(((game / 'AddonPackages').resolve(), library_paths.vault_path(game, data, config)))
        if any(path == p or p in path.parents for p in protected):
            raise ValueError('游戏目录不能位于包库、停用库或管家数据目录内。')
        if disabled_folder is not None or ident is None:
            config = {'mode': 'game', 'folder': library_paths.folder_name(
                disabled_folder if disabled_folder is not None else library_paths.DEFAULT_VAULT)}
            vault = library_paths.vault_path(path, self.data, config)
        else:
            item = self.entry(ident)
            data = self.location(ident)
            config = library_paths.configuration(data, item.get('disabled_folder', library_paths.DEFAULT_VAULT))
            vault = library_paths.vault_path(path, data, config)
        if vault.exists() and not vault.is_dir(): raise ValueError('停用库路径已被文件占用。')
        roots = [(path / 'AddonPackages').resolve(), vault]
        def overlaps(a, b):
            return a == b or a in b.parents or b in a.parents
        # A preserved legacy vault belongs inside this workspace's own data directory.
        checked = roots if config['mode'] == 'game' else roots[:1]
        if overlaps(*roots) or any(overlaps(root, p) for root in checked
                                  for p in [*protected, (path / game_storage.FOLDER).resolve()]):
            raise ValueError('此目录的包库与已有目录或管家数据重叠，无法独立管理；请使用独立包库。')
        if any(vault == Path(item['path']).resolve() or vault in Path(item['path']).resolve().parents
               for item in self.value['items'] if not item.get('archived')):
            raise ValueError('停用库不能包含已保存的游戏目录。')

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
            raise ValueError('目录名称需为 1–80 个字符。')
        return value.strip()

    def rename(self, ident, name):
        self.entry(ident); name = self.unique_name(name, ident)
        self._save({**self.value, 'items': [{**p, 'name': name} if p['id'] == ident else p for p in self.value['items']]})
        return self.public()

    def unique_name(self, value, ident=None):
        name = self.name(value)
        if any(p['id'] != ident and not p.get('archived') and p['name'].strip().casefold() == name.casefold()
               for p in self.value['items']):
            raise ValueError('目录名称已存在，请修改名称后再保存。')
        return name

    def activate(self, ident):
        item = self.entry(ident)
        if not self.available(item): raise ValueError('此游戏目录当前不可用，请检查路径或磁盘连接；已有数据保留。')
        self.check_location(Path(item['path']).resolve(), ident)
        self._save({**self.value, 'current': ident})
