import os
import threading
import unittest
from unittest import mock

import test_manager as fixtures
import test_game_directories as directories
from vampm import folder_picker
from vampm.game_directories import WorkspaceChanged


class DirectoryPickerTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    app = directories.DirectoryTests.app
    second = directories.DirectoryTests.second

    def test_default_name_uses_root_basename_and_duplicate_requires_user_rename(self):
        app = self.app()
        game = self.root / 'other' / self.game.name
        (game / 'AddonPackages').mkdir(parents=True)
        before = app.directories.file.read_bytes()
        with self.assertRaisesRegex(ValueError, '名称已存在'):
            app.post('/api/game-directories/add', {'path': str(game)})
        self.assertEqual(app.directories.file.read_bytes(), before)
        for name in ('GAME', ' game '):
            with self.assertRaisesRegex(ValueError, '名称已存在'):
                app.post('/api/game-directories/add', {'path': str(game), 'name': name})
        result = app.post('/api/game-directories/add', {'path': str(game), 'name': '第二版本'})
        self.assertEqual(result['items'][-1]['name'], '第二版本')
        new = self.root / '全新版本'; (new / 'AddonPackages').mkdir(parents=True)
        self.assertEqual(app.post('/api/game-directories/add', {'path': str(new)})['items'][-1]['name'], '全新版本')

    def test_rename_and_restored_binding_cannot_bypass_name_uniqueness(self):
        app = self.app(); ident, game, _ = self.second(app)
        with self.assertRaisesRegex(ValueError, '名称已存在'):
            app.post('/api/game-directories/rename', {'id': ident, 'name': self.game.name})
        app.post('/api/game-directories/rename', {'id': 'original', 'name': self.game.name})
        app.post('/api/game-directories/remove', {'id': 'original'})
        app.post('/api/game-directories/rename', {'id': ident, 'name': self.game.name})
        with self.assertRaisesRegex(ValueError, '名称已存在'):
            app.post('/api/game-directories/add', {'path': str(self.game)})
        with self.assertRaisesRegex(ValueError, '名称已存在'):
            app.post('/api/game-directories/rebind', {'id': 'original', 'path': str(self.game)})
        app.post('/api/game-directories/rebind', {'id': 'original', 'path': str(self.game), 'name': '原版本'})
        self.assertEqual(app.directories.entry('original')['name'], '原版本')
        self.assertEqual(app.directories.entry(ident)['name'], self.game.name)

    def test_picker_returns_path_without_registering_or_scanning_and_cancel_preserves_state(self):
        app = self.app(); before = app.directories.file.read_bytes()
        with mock.patch('vampm.server.choose_folder', return_value=str(self.game)) as choose:
            result = app.post('/api/game-directories/browse', {'path': str(self.root)})
            choose.assert_called_once_with(str(self.root))
        self.assertEqual(result, {'path': str(self.game), 'name': self.game.name, 'cancelled': False})
        with mock.patch('vampm.server.choose_folder', return_value=None):
            self.assertEqual(app.post('/api/game-directories/browse', {}), {'cancelled': True})
        self.assertEqual(app.directories.file.read_bytes(), before)
        self.assertEqual(app.c.stats()['files'], 0)

    def test_picker_rejects_non_game_folder_and_releases_dialog_lock_after_failure(self):
        app = self.app()
        with mock.patch('vampm.server.choose_folder', return_value=str(self.root)):
            with self.assertRaisesRegex(ValueError, 'AddonPackages'):
                app.post('/api/game-directories/browse', {})
        with mock.patch('vampm.server.choose_folder', side_effect=OSError('native failure')):
            with self.assertRaises(OSError): app.post('/api/game-directories/browse', {})
        self.assertTrue(app.folder_picker_lock.acquire(blocking=False)); app.folder_picker_lock.release()

    def test_open_dialog_does_not_block_status_or_switch_and_stale_result_is_rejected(self):
        app = self.app(); ident, game, _ = self.second(app)
        entered, release = threading.Event(), threading.Event()
        results = []
        def choose(initial):
            entered.set(); release.wait(5); return str(game)
        def browse():
            try: results.append(app.post('/api/game-directories/browse', {}))
            except Exception as error: results.append(error)
        with mock.patch('vampm.server.choose_folder', side_effect=choose):
            worker = threading.Thread(target=browse, daemon=True); worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(app.workspace_lock.acquire(timeout=1))
                app.workspace_lock.release()
                self.assertEqual(app.get('/api/status', {})['workspace']['id'], 'original')
                with self.assertRaisesRegex(ValueError, '已打开'): app.post('/api/game-directories/browse', {})
                app.post('/api/game-directories/switch', {'id': ident})
            finally:
                release.set(); worker.join(5)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], WorkspaceChanged)

    @unittest.skipUnless(os.name == 'nt', 'Native Windows COM integration')
    def test_native_picker_initializes_and_cancels_without_opening_a_desktop_window(self):
        original = folder_picker._call
        calls = []
        def invoke(pointer, index, types=(), args=()):
            calls.append(index)
            if index == 3: return -2147023673  # HRESULT_FROM_WIN32(ERROR_CANCELLED)
            return original(pointer, index, types, args)
        with mock.patch('vampm.folder_picker._call', side_effect=invoke):
            self.assertIsNone(folder_picker.choose_folder(str(self.game)))
        self.assertIn(12, calls)  # The real IFileDialog accepted the initial folder.
        self.assertEqual(calls.count(2), 2)  # Folder and dialog released.

    @unittest.skipUnless(os.name == 'nt', 'Native Windows COM integration')
    def test_native_picker_reads_unicode_shell_item_path_and_releases_resources(self):
        selected = self.root / '中文 游戏目录'; selected.mkdir()
        original = folder_picker._call
        calls = []
        def invoke(pointer, index, types=(), args=()):
            calls.append(index)
            if index == 3: return 0  # Simulate accepting the dialog, without showing UI.
            if index == 20: return original(pointer, 13, types, args)  # Initial folder as result.
            return original(pointer, index, types, args)
        with mock.patch('vampm.folder_picker._call', side_effect=invoke):
            self.assertEqual(folder_picker.choose_folder(str(selected)), str(selected.resolve()))
        self.assertIn(5, calls)
        self.assertEqual(calls.count(2), 3)
