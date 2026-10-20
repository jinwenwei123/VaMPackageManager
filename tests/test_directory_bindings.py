import shutil
import unittest
from unittest import mock

import test_manager as fixtures
import test_game_directories as directories
from vampm.game_directories import GameDirectories, WorkspaceChanged
from vampm.server import Application
from vampm.util import Job, atomic_json, read_json, sha256


class BindingTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan
    app = directories.DirectoryTests.app
    second = directories.DirectoryTests.second
    directory_alias = directories.DirectoryTests.directory_alias

    def remove(self, app, ident):
        return app.post('/api/game-directories/remove', {'id': ident})

    def rebind(self, app, ident, path):
        return app.post('/api/game-directories/rebind', {'id': ident, 'path': str(path)})

    def test_remove_inactive_directory_preserves_all_data_and_readding_reuses_identity(self):
        app = self.app(); ident, game, file = self.second(app)
        app.post('/api/game-directories/switch', {'id': ident})
        app.active.c.scan(Job('scan')); app.active.c.label('shared.look.1', summary='旧简介', tags=['旧标签'], favorite=True)
        app.active.ops.save_profile('旧方案', ['shared.look.1'])
        app.active.agent._entry('user', '原对话'); app.active.agent._save()
        app.active.home_layout.save({'cards': ['directories', 'recent']})
        app.active.chat_presets.save({'revision': 0, 'action': 'save', 'title': '旧预设', 'prompt': '旧提示词'})
        with mock.patch('vampm.library_paths.require_game_closed'):
            app.post('/api/game-directories/library-folders', {'disabled_folder': '原停用名称'})
        app.post('/api/game-directories/switch', {'id': 'original'})
        data = app.directories.location(ident)
        before = {p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()}
        digest = sha256(file); epoch = app.workspace_epoch
        result = self.remove(app, ident)
        self.assertEqual([p['id'] for p in result['directories']['items']], ['original'])
        self.assertEqual(result['directories']['archived'][0]['id'], ident)
        self.assertEqual(app.workspace_epoch, epoch)
        self.assertEqual(before, {p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()})
        with self.assertRaises(ValueError): app.post('/api/game-directories/switch', {'id': ident})
        result = app.post('/api/game-directories/add', {'path': str(game), 'disabled_folder': '.未启用模组'})
        self.assertEqual(result['items'][-1]['id'], ident)
        self.assertEqual(result['items'][-1]['disabled_folder'], '原停用名称')
        self.assertEqual(result['archived'], [])
        app.post('/api/game-directories/switch', {'id': ident})
        self.assertEqual(app.active.c.data, data)
        detail = app.active.c.detail('shared.look.1')
        self.assertEqual(detail['summary'], '旧简介'); self.assertEqual(detail['tags'], ['旧标签']); self.assertTrue(detail['favorite'])
        self.assertTrue(any(p['name'] == '旧方案' for p in app.active.ops.profiles()))
        self.assertEqual(app.active.agent.data['messages'][0]['text'], '原对话')
        self.assertEqual(app.active.home_layout.get()['cards'], ['directories', 'recent'])
        self.assertTrue(any(p['title'] == '旧预设' for p in app.active.chat_presets.get()['items']))
        self.assertEqual(sha256(file), digest)

    def test_last_current_directory_can_be_removed_and_empty_state_survives_restart(self):
        file = self.put(); self.scan(); app = self.app()
        app.c.label('author.scene.1', summary='保留原目录')
        old_data, epoch = app.c.data, app.workspace_epoch
        self.remove(app, 'original')
        self.assertIsNone(app.active.workspace_id)
        self.assertIsNone(app.directories.value['current'])
        status = app.get('/api/status', {})
        self.assertFalse(status['workspace']['available'])
        self.assertEqual(status['stats']['files'], 0)
        self.assertEqual(status['stats']['game'], '')
        self.assertEqual(status['game_directories']['items'], [])
        self.assertTrue(file.exists())
        self.assertTrue((old_data / 'catalog.sqlite3').exists())
        self.assertNotEqual(app.active.c.data, old_data)
        self.assertFalse(app.active.c.game.exists())
        with self.assertRaises(WorkspaceChanged): app.post('/api/scan', {}, scope=epoch)
        for route in ('/api/scan', '/api/agent/send', '/api/ai-queue/start', '/api/launch'):
            with self.assertRaisesRegex(ValueError, '选择游戏目录'): app.post(route, {})
        app.post('/api/appearance', {'palette': 'ocean', 'mode': 'light'})
        restored = self.app()
        self.assertIsNone(restored.active.workspace_id)
        restored.post('/api/game-directories/add', {'path': str(self.game)})
        restored.post('/api/game-directories/switch', {'id': 'original'})
        self.assertEqual(restored.active.c.data, old_data)
        self.assertEqual(restored.active.c.detail('author.scene.1')['summary'], '保留原目录')

    def test_current_rebind_after_move_preserves_vault_labels_profiles_and_operation_recovery(self):
        file = self.put(folder='原文件夹'); digest = sha256(file)
        app = self.app(); app.c.scan(Job('scan')); app.c.label('author.scene.1', summary='搬家保留')
        app.ops.save_profile('搬家方案', ['author.scene.1'])
        with mock.patch('vampm.library_paths.require_game_closed'):
            app.post('/api/game-directories/library-folders', {'disabled_folder': '自定义停用库'})
        old = app.active
        plan = old.ops.plan('organize', ['author.scene.1'], Job('plan'))
        old.ops.apply(plan['id'], Job('move'))
        manifest = old.ops.directory / plan['id'] / 'manifest.json'
        record = read_json(manifest); record['status'] = 'interrupted'; atomic_json(manifest, record)
        new = self.root / '搬家后'; self.game.rename(new)
        epoch = app.workspace_epoch
        self.rebind(app, 'original', new)
        self.assertEqual(old.c.game, self.game)
        self.assertEqual(app.active.c.game, new)
        self.assertEqual(app.active.c.roots['vault'], new / '自定义停用库')
        self.assertEqual(app.active.c.data, old.c.data)
        self.assertFalse(self.game.exists())
        self.assertEqual(app.active.c.detail('author.scene.1')['summary'], '搬家保留')
        self.assertTrue(any(p['name'] == '搬家方案' for p in app.active.ops.profiles()))
        with self.assertRaises(WorkspaceChanged): app.post('/api/label', {}, scope=epoch)
        app.active.ops.undo(plan['id'], Job('recover after move'))
        self.assertEqual(sha256(new / file.relative_to(self.game)), digest)
        self.assertEqual(self.app().active.c.game, new)

    def test_archived_data_can_bind_new_location_without_new_id_or_automatic_scan(self):
        app = self.app(); ident, game, file = self.second(app)
        app.post('/api/game-directories/switch', {'id': ident})
        app.active.c.scan(Job('scan')); app.active.c.label('shared.look.1', summary='归档的数据')
        data = app.active.c.data
        self.remove(app, ident)
        moved = self.root / 'moved'; game.rename(moved)
        result = self.rebind(app, ident, moved)
        self.assertIsNone(result['workspace']['id'])  # Restoring a saved entry does not auto-switch.
        self.assertEqual(result['directories']['archived'], [])
        app.post('/api/game-directories/switch', {'id': ident})
        self.assertEqual(app.active.c.data, data)
        self.assertEqual(app.active.c.detail('shared.look.1')['summary'], '归档的数据')
        self.assertEqual(app.active.c.roots['active'], moved / 'AddonPackages')
        self.assertFalse(game.exists())

    def test_rebinding_cannot_overwrite_another_data_binding_or_share_its_library(self):
        app = self.app(); ident, game, _ = self.second(app)
        with self.assertRaisesRegex(ValueError, '另一份'): self.rebind(app, 'original', game)
        alias = self.root / 'alias'; self.directory_alias(alias, game)
        with self.assertRaises(ValueError): self.rebind(app, 'original', alias)
        inside = self.game / 'AddonPackages' / 'nested'; (inside / 'AddonPackages').mkdir(parents=True)
        with self.assertRaises(ValueError): self.rebind(app, 'original', inside)
        with self.assertRaises(ValueError): app.post('/api/game-directories/remove', {})
        with self.assertRaises(ValueError): app.post('/api/game-directories/rebind', {'path': str(game)})
        self.assertEqual(app.active.workspace_id, 'original')
        self.assertEqual(app.active.c.game, self.game)
        # Removing the other binding frees the path, while keeping both data identities.
        self.remove(app, ident)
        shutil.copytree(app.c.storage.base, game / '.PackageManagerStorage' / app.c.storage.value['owner'])
        self.rebind(app, 'original', game)
        self.assertEqual(app.active.c.game, game)
        registry = GameDirectories(self.home, self.game)
        self.assertEqual(registry.public()['archived'][0]['id'], ident)
        self.assertEqual(registry.public()['items'][0]['id'], 'original')
        self.remove(app, 'original')
        with self.assertRaisesRegex(ValueError, '多份保留'):
            app.post('/api/game-directories/add', {'path': str(game)})
        self.rebind(app, 'original', game)  # Explicit data selection resolves ambiguity.
        self.assertEqual(len(app.directories.public()['archived']), 1)

    def test_missing_custom_vault_blocks_binding_without_modifying_data(self):
        self.put(); self.scan(); app = self.app()
        plan = app.ops.plan('organize', ['author.scene.1'], Job('plan')); app.ops.apply(plan['id'], Job('apply'))
        target = self.root / 'incomplete'; (target / 'AddonPackages').mkdir(parents=True)
        before = app.directories.file.read_bytes()
        with self.assertRaisesRegex(ValueError, '缺少停用文件夹'): self.rebind(app, 'original', target)
        self.assertEqual(app.directories.file.read_bytes(), before)
        self.assertEqual(app.active.c.game, self.game)
        self.assertEqual(app.active.c.stats()['files'], 1)
        self.assertFalse((target / '.未启用模组').exists())

    def test_failed_registry_save_and_busy_tasks_leave_current_binding_usable(self):
        app = self.app(); target = self.root / 'target'; (target / 'AddonPackages').mkdir(parents=True)
        shutil.copytree(app.c.storage.base, target / '.PackageManagerStorage' / app.c.storage.value['owner'])
        before = app.directories.file.read_bytes(); epoch = app.workspace_epoch
        for action in (lambda: self.remove(app, 'original'), lambda: self.rebind(app, 'original', target)):
            with mock.patch.object(app.directories, '_save', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): action()
            self.assertEqual(app.directories.file.read_bytes(), before)
            self.assertIs(app.active, app); self.assertFalse(app.ai.closed)
            self.assertEqual(app.workspace_epoch, epoch)
            for lock in (app.jobs.lock, app.agent.turn_lock):
                lock.acquire()
                try:
                    with self.assertRaisesRegex(ValueError, '任务'): action()
                finally: lock.release()

    def test_portable_tool_and_data_move_with_game_and_rebind_in_place(self):
        game = self.root / 'portable'; (game / 'AddonPackages').mkdir(parents=True)
        home = game / 'Manager'
        app = Application(game, home)
        (game / 'AddonPackages' / 'A.Look.1.var').write_bytes(fixtures.var_bytes('A.Look.1'))
        app.c.scan(Job('scan')); app.c.label('a.look.1', summary='随管家迁移的数据')
        app.agent.close(); app.ai.close()
        target = self.root / 'portable-moved'; game.rename(target)
        moved = Application(target, target / 'Manager')
        self.addCleanup(lambda: moved.active.ai.close()); self.addCleanup(lambda: moved.active.agent.close())
        self.assertFalse(moved.workspace_info()['available'])
        self.rebind(moved, 'original', target)
        self.assertTrue(moved.active.workspace_info()['available'])
        self.assertEqual(moved.active.c.detail('a.look.1')['summary'], '随管家迁移的数据')
        self.assertEqual(moved.active.c.data, target / 'Manager' / 'data')
        self.assertFalse(game.exists())

    def test_removed_original_and_other_directories_can_all_stay_archived(self):
        app = self.app(); ident, _, _ = self.second(app)
        self.remove(app, ident); self.remove(app, 'original')
        registry = GameDirectories(self.home, self.game)
        self.assertEqual(registry.public()['items'], [])
        self.assertEqual(len(registry.public()['archived']), 2)
        self.assertIsNone(registry.value['current'])
        invalid = {**registry.value, 'current': 'original'}
        atomic_json(registry.file, invalid)
        with self.assertRaises(ValueError): GameDirectories(self.home, self.game)
