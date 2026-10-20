import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_manager as fixtures
from test_file_moves import Interrupted
from vampm.catalog import Catalog
from vampm.game_storage import CONFIG, FOLDER, check_binding
from vampm.importer import Importer
from vampm.operations import Operations
from vampm.quarantine import Quarantine
from vampm.server import Application
from vampm.util import Job, read_json, atomic_json, sha256


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.game = self.root / 'game'; self.home = self.root / 'tool'
        (self.game / 'AddonPackages').mkdir(parents=True)
        self.data = self.home / 'data'; self.data.mkdir(parents=True)
        for module in ('operations', 'importer', 'library_paths'):
            patcher = mock.patch('vampm.' + module + '.require_game_closed')
            patcher.start(); self.addCleanup(patcher.stop)

    def legacy(self, kind):
        folder = self.data / kind; folder.mkdir(exist_ok=True)
        (folder / 'empty').mkdir(); (folder / 'keep.txt').write_bytes(b'legacy bytes')
        return folder

    def catalog(self):
        return Catalog(self.game, self.home)

    def app(self):
        app = Application(self.game, self.home)
        self.addCleanup(lambda: app.active.ai.close()); self.addCleanup(lambda: app.active.agent.close())
        return app

    def test_new_storage_uses_game_english_paths_and_keeps_metadata_in_data(self):
        c = self.catalog()
        self.assertEqual(c.roots['vault'], self.game / '.DisabledPackages')
        self.assertEqual(c.storage.public()['legacy'], [])
        importer = Importer(c, None, None)
        data = fixtures.var_bytes()
        uid = importer.upload(io.BytesIO(data), 'Author.Scene.1.var', len(data))['id']
        self.assertEqual((importer.staging / (uid + '.var')).read_bytes(), data)
        self.assertTrue(importer.staging.is_relative_to(self.game / FOLDER))
        self.assertTrue(c.dbpath.is_relative_to(self.data))
        self.assertFalse((self.data / 'imports').exists())
        self.assertEqual(self.catalog().storage.base, c.storage.base)

    def test_configured_folder_can_be_renamed_without_losing_contents(self):
        old = self.game / '.未启用模组'; old.mkdir()
        (old / 'keep.txt').write_bytes(b'keep')
        c = Catalog(self.game, self.home, disabled_folder='.未启用模组')
        self.assertEqual(c.roots['vault'], old)
        from vampm.library_paths import change
        change(c, '.DisabledPackages')
        self.assertFalse(old.exists())
        self.assertEqual((c.roots['vault'] / 'keep.txt').read_bytes(), b'keep')
        self.assertEqual(self.catalog().roots['vault'], self.game / '.DisabledPackages')

    def test_old_quarantine_migration_preserves_history_labels_and_undo(self):
        old = self.legacy('quarantine')
        c = self.catalog(); ops = Operations(c)
        contents = fixtures.var_bytes()
        originals = [self.game / 'AddonPackages' / str(i) / 'Author.Scene.1.var' for i in range(2)]
        for path in originals: path.parent.mkdir(); path.write_bytes(contents)
        c.scan(Job('scan')); c.label('author.scene.1', summary='keep summary', tags=['keep tag'])
        c.duplicate_audit(Job('audit'))
        plan = ops.plan('dedup', [], Job('plan')); ops.apply(plan['id'], Job('apply'))
        history = {p.relative_to(ops.directory): p.read_bytes() for p in ops.directory.rglob('*') if p.is_file()}
        c.storage.migrate(Job('migrate'))
        self.assertFalse(old.exists()); self.assertEqual(c.storage.public()['legacy'], [])
        self.assertEqual((c.roots['quarantine'] / 'keep.txt').read_bytes(), b'legacy bytes')
        self.assertTrue((c.roots['quarantine'] / 'empty').is_dir())
        self.assertEqual(history, {p.relative_to(ops.directory): p.read_bytes() for p in ops.directory.rglob('*') if p.is_file()})
        self.assertEqual(Quarantine(c, ops).listing()['count'], 2)
        ops.undo(plan['id'], Job('restore historical batch'))
        for path in originals: self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(c.detail('author.scene.1')['summary'], 'keep summary')
        self.assertEqual(c.detail('author.scene.1')['tags'], ['keep tag'])
        self.assertEqual(self.catalog().roots['quarantine'], c.roots['quarantine'])

    def test_legacy_uploaded_file_is_installable_after_migration(self):
        self.legacy('imports'); c = self.catalog(); importer = Importer(c, None, None)
        data = fixtures.var_bytes(); uid = importer.upload(io.BytesIO(data), 'Author.Scene.1.var', len(data))['id']
        c.storage.migrate(Job('migrate'))
        result = importer.run([uid], Job('import'), use_ai=False, find=False)
        self.assertEqual(result['installed'], 1)
        self.assertEqual(next(c.roots['vault'].rglob('*.var')).read_bytes(), data)

    def test_cancelled_migration_stays_blocked_then_resumes(self):
        self.legacy('imports'); self.legacy('quarantine'); app = self.app(); storage = app.c.storage
        from vampm.util import Cancelled
        with mock.patch('vampm.game_storage.move_no_replace', side_effect=Cancelled('cancelled')):
            with self.assertRaises(Cancelled): storage.migrate(Job('cancel'))
        self.assertTrue(storage.public()['pending'])
        with self.assertRaisesRegex(ValueError, '迁移'): app.post('/api/scan', {})
        with self.assertRaisesRegex(ValueError, '迁移'): app.post('/api/game-directories/remove', {'id': 'original'})
        self.assertTrue(app.get('/api/status', {})['workspace']['storage']['pending'])
        started = app.post('/api/game-storage/migrate', {}); app.jobs.wait()
        self.assertEqual(app.jobs.jobs[started['id']].status, 'done')
        self.assertFalse(storage.public()['pending']); self.assertEqual(storage.public()['legacy'], [])

    def test_crash_after_move_before_config_commit_can_resume(self):
        self.legacy('downloads'); c = self.catalog(); storage = c.storage
        with mock.patch.object(storage, '_save', side_effect=Interrupted):
            with self.assertRaises(Interrupted): storage.migrate(Job('move'))
        self.assertFalse((self.data / 'downloads').exists()); self.assertTrue(storage.journal.exists())
        c = self.catalog(); c.storage.migrate(Job('resume'))
        self.assertEqual((c.storage.path('downloads') / 'keep.txt').read_bytes(), b'legacy bytes')
        self.assertFalse(c.storage.journal.exists())

    def test_existing_target_is_not_merged_or_overwritten(self):
        old = self.legacy('imports'); c = self.catalog()
        target = c.storage.base / 'imports'; target.mkdir(); (target / 'keep.txt').write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, '已存在'): c.storage.migrate(Job('migrate'))
        self.assertEqual((old / 'keep.txt').read_bytes(), b'legacy bytes')
        self.assertEqual((target / 'keep.txt').read_bytes(), b'different')
        self.assertFalse(c.storage.journal.exists())

    def test_game_relocation_requires_management_folder_and_retains_owner(self):
        c = self.catalog(); owner = c.storage.value['owner']
        (c.storage.ensure('quarantine') / 'keep').write_bytes(b'keep')
        incomplete = self.root / 'incomplete'; (incomplete / 'AddonPackages').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, '.PackageManagerStorage'): check_binding(self.data, self.game, incomplete)
        moved = self.root / 'moved'; self.game.rename(moved)
        check_binding(self.data, self.game, moved)
        restored = Catalog(moved, self.home)
        self.assertEqual(restored.storage.value['owner'], owner)
        self.assertEqual((restored.roots['quarantine'] / 'keep').read_bytes(), b'keep')

    def test_missing_storage_is_not_silently_recreated(self):
        c = self.catalog(); c.storage.base.rename(self.root / 'detached')
        c = self.catalog()
        with self.assertRaisesRegex(ValueError, '缺失'): c.storage.ensure('imports')
        with self.assertRaisesRegex(ValueError, '缺失'):
            Operations(c)._path({'to_area': 'quarantine', 'to_rel': 'file'}, 'to')
        self.assertFalse(c.storage.base.exists())

    def test_different_manager_data_directories_have_separate_bulk_storage(self):
        c = self.catalog(); other = Catalog(self.game, self.root / 'another-tool')
        self.assertNotEqual(c.storage.base, other.storage.base)
        self.assertEqual(c.storage.base.parent, other.storage.base.parent)

    def test_nested_game_and_custom_vault_cannot_enter_storage(self):
        app = self.app(); nested = app.c.storage.base / 'nested'; (nested / 'AddonPackages').mkdir(parents=True)
        with self.assertRaises(ValueError): app.post('/api/game-directories/add', {'path': str(nested)})
        with self.assertRaises(ValueError): app.post('/api/game-directories/library-folders', {'disabled_folder': FOLDER})

    def test_real_cross_volume_legacy_storage_migration(self):
        import os
        other = Path('/dev/shm') if os.name != 'nt' else Path(__file__).resolve().parents[1] / 'test-results'
        if not other.is_dir(): self.skipTest('Second filesystem unavailable')
        if other.stat().st_dev == self.root.stat().st_dev: self.skipTest('A second filesystem is required')
        with tempfile.TemporaryDirectory(dir=other) as folder:
            self.game = Path(folder) / 'game'; (self.game / 'AddonPackages').mkdir(parents=True)
            self.legacy('imports'); self.legacy('quarantine'); self.legacy('downloads')
            c = self.catalog(); c.storage.migrate(Job('actual cross volume'))
            for kind in ('imports', 'quarantine', 'downloads'):
                self.assertEqual((c.storage.path(kind) / 'keep.txt').read_bytes(), b'legacy bytes')
                self.assertFalse((self.data / kind).exists())
            self.assertFalse(list(self.game.rglob('.vampm-transfer-*')))

    def test_published_cross_volume_operation_can_be_undone_after_cleanup_failure(self):
        import errno
        from vampm import file_moves
        c = self.catalog(); ops = Operations(c)
        source = self.game / 'AddonPackages' / 'Author.Scene.1.var'; contents = fixtures.var_bytes(); source.write_bytes(contents)
        c.scan(Job('scan')); plan = ops.plan('organize', ['author.scene.1'], Job('plan'))
        rename = file_moves._rename
        def across(src, dst):
            if not src.name.startswith(file_moves.PREFIX): raise OSError(errno.EXDEV, 'fixture')
            return rename(src, dst)
        with mock.patch.object(file_moves, '_rename', side_effect=across):
            with mock.patch.object(file_moves, '_finish', side_effect=OSError('cleanup failed')):
                with self.assertRaises(OSError): ops.apply(plan['id'], Job('apply'))
            self.assertEqual(source.read_bytes(), contents)
            ops.undo(plan['id'], Job('undo'))
        self.assertEqual(source.read_bytes(), contents)
        self.assertFalse(list(c.roots['vault'].rglob('*.var')))
        self.assertFalse(list(self.game.rglob('.vampm-transfer-*')))
        self.assertEqual(c.detail('author.scene.1')['files'][0]['area'], 'active')

    def test_import_cleanup_preserves_staging_when_transfer_receipt_remains(self):
        import errno
        from vampm import file_moves
        c = self.catalog(); importer = Importer(c, None, None)
        contents = fixtures.var_bytes(); uid = importer.upload(io.BytesIO(contents), 'Author.Scene.1.var', len(contents))['id']
        rename = file_moves._rename
        def across(src, dst):
            if src.parent == importer.staging: raise OSError(errno.EXDEV, 'fixture')
            return rename(src, dst)
        with mock.patch.object(file_moves, '_rename', side_effect=across), mock.patch.object(file_moves, '_finish', side_effect=OSError('cleanup failed')):
            result = importer.run([uid], Job('import'), use_ai=False, find=False)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['retained_staging'][0]['id'], uid)
        self.assertEqual((importer.staging / (uid + '.var')).read_bytes(), contents)
        self.assertTrue((importer.staging / (uid + '.json')).exists())
        target = Path(result['retained_staging'][0]['destination'])
        self.assertEqual(target.read_bytes(), contents)
        file_moves.recover_move(importer.staging / (uid + '.var'), target, finish=True)
        self.assertFalse((importer.staging / (uid + '.var')).exists())

    def test_real_cross_volume_legacy_vault_rename_and_crash_rollback(self):
        import os
        from vampm import library_paths
        other = Path('/dev/shm') if os.name != 'nt' else Path(__file__).resolve().parents[1] / 'test-results'
        if not other.is_dir() or other.stat().st_dev == self.root.stat().st_dev:
            self.skipTest('A second filesystem is required')
        with tempfile.TemporaryDirectory(dir=other) as folder:
            self.game = Path(folder) / 'game'; (self.game / 'AddonPackages').mkdir(parents=True)
            old = self.legacy('disabled'); c = self.catalog()
            with mock.patch.object(library_paths, 'save_configuration', side_effect=Interrupted):
                with self.assertRaises(Interrupted): library_paths.change(c, '.DisabledPackages')
            self.assertFalse(old.exists())
            restored = self.catalog()  # Original metadata commit did not complete: restore the old path.
            self.assertEqual(restored.roots['vault'], old)
            self.assertEqual((old / 'keep.txt').read_bytes(), b'legacy bytes')
            self.assertFalse((self.game / '.DisabledPackages').exists())
            library_paths.change(restored, '.DisabledPackages')
            self.assertEqual((restored.roots['vault'] / 'keep.txt').read_bytes(), b'legacy bytes')
            self.assertFalse(old.exists())

    def test_invalid_owner_config_never_silently_allocates_another_storage(self):
        atomic_json(self.data / CONFIG, None)
        with self.assertRaisesRegex(ValueError, '配置无效'): self.catalog()
        self.assertFalse((self.game / FOLDER).exists())

    def test_invalid_migration_journal_does_not_move_files(self):
        old = self.legacy('quarantine'); c = self.catalog()
        atomic_json(c.storage.journal, None)
        with self.assertRaisesRegex(ValueError, '记录无效'): c.storage.migrate(Job('resume'))
        self.assertEqual((old / 'keep.txt').read_bytes(), b'legacy bytes')
        self.assertTrue(c.storage.journal.exists())
