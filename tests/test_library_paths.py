import unittest
from unittest import mock

import test_manager as fixtures
import test_game_directories as directories
from vampm import library_paths
from vampm.catalog import Catalog
from vampm.game_directories import WorkspaceChanged
from vampm.server import Application
from vampm.util import Job, atomic_json, sha256


class LibraryPathTests(unittest.TestCase):
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan
    tearDown = fixtures.Workspace.tearDown
    app = directories.DirectoryTests.app
    directory_alias = directories.DirectoryTests.directory_alias

    def setUp(self):
        fixtures.Workspace.setUp(self)
        self.patchers.append(mock.patch('vampm.library_paths.require_game_closed'))
        self.patchers[-1].start()

    def change(self, app, folder):
        return app.post('/api/game-directories/library-folders', {'disabled_folder': folder})

    def test_new_games_default_and_custom_paths_are_relative_and_isolated(self):
        app = self.app()
        for index, name in enumerate(('.DisabledPackages', '备用模组')):
            game = self.root / str(index)
            (game / 'AddonPackages').mkdir(parents=True)
            (game / name).mkdir()
            inactive = game / name / 'A.Parked.1.var'
            inactive.write_bytes(fixtures.var_bytes('A.Parked.1'))
            data = {'path': str(game)}
            if index: data['disabled_folder'] = name
            item = app.post('/api/game-directories/add', data)['items'][-1]
            self.assertEqual(item['disabled_folder'], name)
            self.assertEqual(item['active_folder'], 'AddonPackages')
            self.assertFalse(app.directories.location(item['id']).exists())
            app.post('/api/game-directories/switch', {'id': item['id']})
            self.assertEqual(app.active.c.roots['active'], game / 'AddonPackages')
            self.assertEqual(app.active.c.roots['vault'], game / name)
            app.active.c.scan(Job('scan'))
            self.assertEqual(app.active.c.stats()['files'], 1)
            self.assertEqual(app.active.c.stats()['active'], 0)
            app.active.c.label('a.parked.1', summary=name, tags=[name])
        restored = self.app()
        self.assertEqual(restored.c.roots['vault'], self.root / '1' / '备用模组')
        self.assertEqual(restored.c.detail('a.parked.1')['summary'], '备用模组')
        app.post('/api/game-directories/switch', {'id': 'original'})
        self.assertEqual(app.active.c.roots['vault'], self.game / '.DisabledPackages')

    def test_rename_retains_resources_metadata_profiles_and_old_operation_undo(self):
        original = self.put(folder='原位置')
        digest = sha256(original)
        app = self.app(); app.c.scan(Job('scan'))
        app.c.label('author.scene.1', summary='保留简介', tags=['标签'])
        app.ops.save_profile('保留方案', ['author.scene.1'])
        plan = app.ops.plan('organize', ['author.scene.1'], Job('plan'))
        app.ops.apply(plan['id'], Job('move'))
        old = app.c.roots['vault']
        attachment = old / '散落资料' / '说明.txt'
        attachment.parent.mkdir(); attachment.write_text('全部保留', encoding='utf8')
        epoch = app.workspace_epoch
        self.change(app, '我的停用资源')
        current = app.active
        self.assertEqual(app.c.roots['vault'], old)  # Old reads keep their own session.
        self.assertEqual(current.c.roots['vault'], self.game / '我的停用资源')
        self.assertFalse(old.exists())
        self.assertEqual((current.c.roots['vault'] / attachment.relative_to(old)).read_text(encoding='utf8'), '全部保留')
        self.assertEqual(current.c.detail('author.scene.1')['summary'], '保留简介')
        self.assertEqual(current.c.detail('author.scene.1')['tags'], ['标签'])
        self.assertTrue(any(p['name'] == '保留方案' for p in current.ops.profiles()))
        self.assertEqual(app.get('/api/game-directories', {})['items'][0]['disabled_folder'], '我的停用资源')
        with self.assertRaises(WorkspaceChanged):
            app.post('/api/scan', {}, scope=epoch)
        current.ops.undo(plan['id'], Job('undo historical move'))
        self.assertEqual(sha256(original), digest)
        plan = current.ops.plan('organize', ['author.scene.1'], Job('plan again'))
        current.ops.apply(plan['id'], Job('move again'))
        self.assertEqual(sha256(next(current.c.roots['vault'].rglob('*.var'))), digest)
        self.assertEqual(self.app().c.roots['vault'], current.c.roots['vault'])

    def test_legacy_location_is_preserved_until_explicit_migration(self):
        old = self.c.data / 'disabled'; old.mkdir()
        file = old / 'A.Legacy.1.var'; contents = fixtures.var_bytes('A.Legacy.1'); file.write_bytes(contents)
        with self.c.db() as db:
            db.execute("DELETE FROM settings WHERE key IN ('disabled_location','disabled_folder')")
        app = self.app()
        self.assertEqual(app.c.roots['vault'], old)
        app.c.scan(Job('scan')); app.c.label('a.legacy.1', summary='旧数据')
        self.change(app, '.DisabledPackages')
        self.assertFalse(old.exists())
        self.assertEqual((self.game / '.DisabledPackages' / file.name).read_bytes(), contents)
        self.assertEqual(app.active.c.detail('a.legacy.1')['summary'], '旧数据')
        self.assertEqual(self.app().c.roots['vault'], self.game / '.DisabledPackages')

    def test_missing_vault_can_change_without_scanning_or_recreating_game(self):
        app = self.app()
        self.change(app, '待用')
        self.assertEqual(app.active.c.roots['vault'], self.game / '待用')
        self.assertFalse((self.game / '待用').exists())
        self.assertEqual(app.active.c.stats()['files'], 0)
        self.game.rename(self.root / 'unplugged')
        with self.assertRaisesRegex(ValueError, '不可用'): self.change(app, '不应创建')
        self.assertFalse(self.game.exists())

    def test_invalid_names_fixed_active_folder_and_existing_targets_are_rejected(self):
        app = self.app()
        invalid = ['', None, '../escape', 'a/b', 'a\\b', '/absolute', 'C:drive', ' ends ',
                   'ends.', 'a:b', 'a*', 'NUL.txt', 'COM1', 'AddonPackages', 'addonpackages',
                   'Saves', 'Custom', 'VaM_Data', 'VaMPackageManager', 'a' * 81]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError): self.change(app, value)
        with self.assertRaisesRegex(ValueError, '固定'):
            app.post('/api/game-directories/library-folders', {'disabled_folder': 'safe', 'active_folder': 'other'})
        with self.assertRaisesRegex(ValueError, '固定'):
            app.post('/api/game-directories/add', {'path': str(self.game), 'active_folder': 'other'})
        destination = self.game / '已有内容'; destination.mkdir()
        sentinel = destination / 'keep.txt'; sentinel.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, '已存在'): self.change(app, destination.name)
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertIs(app.active, app)
        self.assertEqual(self.app().c.roots['vault'], self.game / '.DisabledPackages')

    def test_custom_vault_alias_and_nested_game_are_rejected(self):
        app = self.app(); self.change(app, '备用')
        nested = self.game / '备用' / 'nested'; (nested / 'AddonPackages').mkdir(parents=True)
        with self.assertRaises(ValueError):
            app.post('/api/game-directories/add', {'path': str(nested)})
        alias = self.game / 'alias'; self.directory_alias(alias, app.active.c.roots['active'])
        with self.assertRaises(ValueError): self.change(app, 'alias')
        other = self.root / 'other'; (other / 'AddonPackages').mkdir(parents=True)
        self.directory_alias(other / 'bad-vault', app.active.c.roots['vault'])
        with self.assertRaises(ValueError):
            app.post('/api/game-directories/add', {'path': str(other), 'disabled_folder': 'bad-vault'})
        # A custom root must not engulf an already registered nested game either.
        nested = self.game / 'versions' / 'nested'; (nested / 'AddonPackages').mkdir(parents=True)
        app.post('/api/game-directories/add', {'path': str(nested)})
        with self.assertRaisesRegex(ValueError, '重叠|包含'): self.change(app, 'versions')

    def test_tasks_pending_operations_and_running_game_block_rename(self):
        app = self.app()
        for lock in (app.jobs.lock, app.agent.turn_lock):
            lock.acquire()
            try:
                with self.assertRaisesRegex(ValueError, '任务'): self.change(app, '待用')
            finally: lock.release()
        with mock.patch.object(app.ops, 'check_pending', side_effect=ValueError('未完成')):
            with self.assertRaisesRegex(ValueError, '未完成'): self.change(app, '待用')
        with mock.patch('vampm.library_paths.require_game_closed', side_effect=ValueError('退出 VaM')):
            with self.assertRaisesRegex(ValueError, '退出 VaM'): self.change(app, '待用')
        self.assertIs(app.active, app)
        self.assertFalse((self.c.data / 'library-path-change.json').exists())

    def test_failed_config_write_rolls_back_rename_and_keeps_original_session(self):
        app = self.app(); old = app.c.roots['vault']; old.mkdir()
        file = old / 'sentinel'; file.write_bytes(b'keep')
        with mock.patch('vampm.library_paths.save_configuration', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'): self.change(app, '待用')
        self.assertEqual(file.read_bytes(), b'keep')
        self.assertFalse((self.game / '待用').exists())
        self.assertFalse((app.c.data / 'library-path-change.json').exists())
        self.assertIs(app.active, app); self.assertFalse(app.ai.closed)

    def interrupted(self, committed=False):
        old = library_paths.configuration(self.c.data)
        new = {'mode': 'game', 'folder': '待用'}
        source = self.c.roots['vault']; source.mkdir()
        (source / 'sentinel').write_bytes(b'keep')
        atomic_json(self.c.data / 'library-path-change.json',
                    {'version': 1, 'old': old, 'new': new, 'had_source': True})
        destination = self.game / new['folder']; source.rename(destination)
        if committed: library_paths.save_configuration(self.c, new)
        return source, destination

    def test_restart_rolls_back_interruption_before_config_commit(self):
        source, destination = self.interrupted()
        fresh = Catalog(self.game, self.home)
        self.assertEqual(fresh.roots['vault'], source)
        self.assertEqual((source / 'sentinel').read_bytes(), b'keep')
        self.assertFalse(destination.exists())
        self.assertFalse((self.c.data / 'library-path-change.json').exists())

    def test_restart_finishes_interruption_after_config_commit(self):
        source, destination = self.interrupted(committed=True)
        fresh = Catalog(self.game, self.home)
        self.assertEqual(fresh.roots['vault'], destination)
        self.assertEqual((destination / 'sentinel').read_bytes(), b'keep')
        self.assertFalse(source.exists())
        self.assertFalse((self.c.data / 'library-path-change.json').exists())

    def test_recovery_never_overwrites_a_conflicting_directory(self):
        source, destination = self.interrupted()
        source.mkdir(); (source / 'sentinel').write_bytes(b'new content')
        with self.assertRaisesRegex(ValueError, '冲突'): Catalog(self.game, self.home)
        self.assertEqual((source / 'sentinel').read_bytes(), b'new content')
        self.assertEqual((destination / 'sentinel').read_bytes(), b'keep')
        self.assertTrue((self.c.data / 'library-path-change.json').exists())
        with self.assertRaisesRegex(ValueError, '路径变更待恢复'): self.ops.check_pending()
