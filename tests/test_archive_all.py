import unittest

import test_manager as fixtures
import test_game_directories as directories
from vampm.util import Job, sha256


class ArchiveAllTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan
    app = directories.DirectoryTests.app

    def test_all_scope_discovers_new_files_and_matches_select_all_then_organize(self):
        self.put('A.First.1', folder='original'); self.scan()
        self.put('B.Unindexed.1', folder='new')
        actual = self.ops.plan('organize', [], Job('one click'), archive_scope='all')
        expected = self.ops.plan('organize', list(self.c.packages()), Job('manual select all'))
        self.assertEqual(actual['archive_packages'], 2)
        self.assertEqual(actual['preview'], expected['preview'])
        self.assertEqual(actual['count'], expected['count'])
        self.assertEqual(actual['warnings'], expected['warnings'])

    def test_favorites_scope_uses_saved_favorites_in_both_libraries_and_is_undoable(self):
        # Apply an explicit activation mode, then add unorganized active/inactive files.
        self.ops.apply(self.ops.plan('restore_all', [], Job('plan mode'))['id'], Job('apply mode'))
        first = self.put('A.First.1', folder='first'); other = self.put('B.Other.1', folder='unselected')
        inactive = self.c.roots['vault'] / 'loose' / 'C.Inactive.1.var'
        inactive.parent.mkdir(parents=True); inactive.write_bytes(fixtures.var_bytes('C.Inactive.1'))
        self.scan()
        for key in ('a.first.1', 'c.inactive.1'): self.c.label(key, favorite=True)
        before = {str(path): sha256(path) for path in (first, other, inactive)}
        actual = self.ops.plan('organize', [], Job('favorites'), archive_scope='favorites')
        expected = self.ops.plan('organize', ['a.first.1', 'c.inactive.1'], Job('manual favorites'))
        self.assertEqual(actual['archive_packages'], 2)
        self.assertEqual(actual['preview'], expected['preview'])
        self.assertFalse(any(move['from_rel'].endswith('B.Other.1.var') for move in actual['preview']))
        self.ops.apply(actual['id'], Job('apply'))
        self.assertEqual(sha256(other), before[str(other)])
        self.ops.undo(actual['id'], Job('undo'))
        for path in (first, other, inactive): self.assertEqual(sha256(path), before[str(path)])

    def test_empty_favorites_do_not_fall_back_to_all_packages(self):
        source = self.put(); self.scan()
        with self.assertRaisesRegex(ValueError, '收藏夹中暂无'):
            self.ops.plan('organize', [], Job('empty'), archive_scope='favorites')
        self.assertTrue(source.exists()); self.assertEqual(self.ops.plans, {})
        with self.assertRaisesRegex(ValueError, '请选择'):
            self.ops.plan('organize', [], Job('old empty selection'))

    def test_scope_cannot_silently_override_explicit_selection_or_other_operations(self):
        source = self.put(); self.scan()
        for kind, keys, scope in [('dedup', [], 'all'), ('organize', ['author.scene.1'], 'all'),
                                  ('organize', [], 'invalid'), ('organize', [], {}), ('organize', [], '')]:
            with self.subTest(kind=kind, scope=scope), self.assertRaises(ValueError):
                self.ops.plan(kind, keys, Job('invalid scope'), archive_scope=scope)
        self.assertTrue(source.exists()); self.assertEqual(self.ops.plans, {})

    def test_route_produces_preview_without_moving_or_requiring_keys(self):
        source = self.put(); app = self.app()
        started = app.post('/api/plan', {'kind': 'organize', 'scope': 'all'})
        app.jobs.wait(); job = app.jobs.jobs[started['id']]
        self.assertEqual(job.status, 'done', job.error)
        self.assertEqual(job.result['archive_scope'], 'all')
        self.assertEqual(job.result['archive_packages'], 1)
        self.assertGreater(job.result['count'], 0)
        self.assertTrue(source.exists())
