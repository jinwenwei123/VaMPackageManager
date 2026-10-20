import subprocess
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.quarantine import Quarantine
from vampm.server import Application
from vampm.util import Cancelled, Job, read_json, sha256


class QuarantineTests(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan

    def batch(self, name='A.Look.1', copies=2):
        data = fixtures.var_bytes(name)
        for i in range(copies): self.put(name, folder=str(i), data=data)
        self.scan(); self.c.duplicate_audit(Job('audit'))
        plan = self.ops.plan('dedup', [], Job('plan'))
        self.ops.apply(plan['id'], Job('dedup'))
        return plan['id']

    def preview(self, quarantine, **selection):
        return quarantine.preview(selection or {'all':True}, Job('preview'))['quarantine_preview']

    def alias(self, link, target, directory=True):
        try: link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if getattr(error, 'winerror', None) != 1314 or not directory: raise
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],check=True,capture_output=True)

    def test_empty_listing_and_actual_inventory_include_untracked_files(self):
        quarantine = Quarantine(self.c, self.ops)
        self.assertEqual(quarantine.listing()['count'], 0)
        token = self.batch()
        extra = self.c.roots['quarantine']/'notes.txt'; extra.write_text('manually stored')
        result = quarantine.listing(token)
        self.assertEqual(result['count'], 2); self.assertEqual(result['total'], 1)
        self.assertEqual({b['id'] for b in result['batches']}, {token, 'untracked'})
        self.assertTrue(next(b for b in result['batches'] if b['id']==token)['can_restore'])
        self.assertNotIn('version', result['items'][0])
        self.assertEqual(result['items'][0]['area'], 'active')

    def test_preview_and_confirmation_then_deletion_preserve_live_files_labels_and_history(self):
        token = self.batch()
        self.c.label('a.look.1', summary='保留简介', tags=['保留标签'], favorite=True)
        before = {str(self.c.path(r)):sha256(self.c.path(r)) for r in self.c.rows()}
        quarantine = Quarantine(self.c, self.ops)
        preview = self.preview(quarantine)
        self.assertEqual(preview['count'], 1)
        self.assertEqual(quarantine.listing()['count'], 1)  # Merely previewing does not delete.
        with self.assertRaises(ValueError): quarantine.purge(preview['id'], False, Job('purge'))
        result = quarantine.purge(preview['id'], True, Job('purge'))
        self.assertEqual(result['count'], 1); self.assertEqual(result['bytes'], preview['bytes'])
        self.assertEqual(quarantine.listing()['count'], 0)
        self.assertEqual(before, {str(self.c.path(r)):sha256(self.c.path(r)) for r in self.c.rows()})
        self.assertEqual(self.c.detail('a.look.1')['summary'], '保留简介')
        self.assertEqual(self.c.detail('a.look.1')['tags'], ['保留标签'])
        self.assertTrue(self.c.detail('a.look.1')['favorite'])
        history = self.ops.history()
        self.assertTrue(all(r['irreversible'] for r in history))
        self.assertFalse(any('files' in r for r in history))
        self.assertEqual(next(r for r in history if r['kind']=='purge_quarantine')['deleted_count'], 1)
        with self.assertRaisesRegex(ValueError,'无法整批恢复'): self.ops.undo(token, Job('undo'))
        with self.c.db() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM files WHERE area='quarantine'").fetchone()[0], 0)
        self.ops.check_pending()

    def test_selected_batch_only_and_new_files_after_preview_are_retained(self):
        first = self.batch(); second = self.batch('B.Look.1')
        quarantine = Quarantine(self.c, self.ops)
        plan = self.preview(quarantine, ids=[first])
        extra = self.c.roots['quarantine']/'new.txt'; extra.write_text('arrived after preview')
        quarantine.purge(plan['id'], True, Job('purge'))
        self.assertTrue(extra.exists())
        self.assertEqual({b['id'] for b in quarantine.listing()['batches']}, {second, 'untracked'})
        self.ops.undo(second, Job('undo'))
        self.assertEqual(quarantine.listing()['count'], 1)
        self.assertEqual(len(self.c.detail('b.look.1')['files']), 2)

    def test_modified_snapshot_or_restored_batch_is_rejected_before_deletion(self):
        token = self.batch()
        quarantine = Quarantine(self.c, self.ops)
        plan = self.preview(quarantine)
        file = next(self.c.roots['quarantine'].rglob('*.var'))
        original = file.read_bytes(); original_stat = file.stat()
        file.write_bytes(original+b'changed')
        with self.assertRaisesRegex(ValueError,'变化'): quarantine.purge(plan['id'], True, Job('purge'))
        self.assertFalse(read_json(self.ops.directory/token/'manifest.json').get('irreversible'))
        file.write_bytes(original)
        import os
        os.utime(file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        plan = self.preview(quarantine)
        self.ops.undo(token, Job('undo'))
        with self.assertRaisesRegex(ValueError,'恢复记录已变化'): quarantine.purge(plan['id'], True, Job('purge'))
        self.assertEqual(len(self.c.detail('a.look.1')['files']), 2)

    def test_interrupted_purge_keeps_remaining_files_and_can_be_previewed_again(self):
        token = self.batch(copies=3)
        quarantine = Quarantine(self.c, self.ops)
        plan = self.preview(quarantine)
        job = Job('purge')
        with mock.patch.object(job, 'update', side_effect=lambda *args:job.cancel.set()):
            with self.assertRaises(Cancelled): quarantine.purge(plan['id'], True, job)
        self.assertEqual(quarantine.listing()['count'], 1)
        row = next(r for r in self.ops.history() if r['kind']=='purge_quarantine')
        self.assertEqual(row['status'], 'partial'); self.assertEqual(row['deleted_count'], 1)
        with self.assertRaisesRegex(ValueError,'无法整批恢复'): self.ops.undo(token, Job('undo'))
        self.ops.check_pending()
        retry = self.preview(quarantine)
        quarantine.purge(retry['id'], True, Job('retry'))
        self.assertEqual(quarantine.listing()['count'], 0)
        self.assertEqual(len(self.c.detail('a.look.1')['files']), 1)

    def test_paths_and_root_links_are_not_followed_and_plans_are_library_scoped(self):
        token = self.batch()
        quarantine = Quarantine(self.c, self.ops)
        outside = self.root/'outside'; outside.mkdir(); sentinel = outside/'keep.txt'; sentinel.write_text('keep')
        self.alias(self.c.roots['quarantine']/'link', outside)
        self.assertEqual(quarantine.listing()['skipped'], 1)
        plan = self.preview(quarantine)
        quarantine.purge(plan['id'], True, Job('purge'))
        self.assertEqual(sentinel.read_text(), 'keep')
        with self.assertRaises(ValueError): self.preview(quarantine, ids=['../outside'])
        app = Application(self.game, self.home)
        other = self.root/'other'; (other/'AddonPackages').mkdir(parents=True)
        ident = app.post('/api/game-directories/add', {'path':str(other)})['items'][-1]['id']
        stray = self.c.roots['quarantine']/'stray.txt'; stray.write_text('original data')
        original_plan = self.preview(app.quarantine)
        app.post('/api/game-directories/switch', {'id':ident})
        self.addCleanup(app.active.ai.close); self.addCleanup(app.active.agent.close)
        self.assertEqual(app.get('/api/quarantine', {})['count'], 0)
        with self.assertRaises(ValueError): app.active.quarantine.purge(original_plan['id'], True, Job('wrong library'))
        self.assertEqual(stray.read_text(), 'original data')
        # A root link must be rejected too, rather than resolved to an outside folder.
        root = app.active.c.roots['quarantine']
        self.alias(root, outside)
        with self.assertRaisesRegex(ValueError,'联接'): app.active.quarantine.listing()

    def test_pending_operation_and_cancelled_preview_never_delete(self):
        token = self.batch()
        quarantine = Quarantine(self.c, self.ops)
        with mock.patch.object(self.ops,'check_pending',side_effect=ValueError('未完成')):
            with self.assertRaises(ValueError): self.preview(quarantine)
        job = Job('cancelled'); job.cancel.set()
        with self.assertRaises(Cancelled): quarantine.preview({'all':True},job)
        self.assertEqual(quarantine.listing()['count'], 1)
        self.ops.undo(token, Job('undo'))

    def test_file_pages_are_bounded_and_preview_does_not_silently_limit_the_deletion_scope(self):
        root = self.c.roots['quarantine']; root.mkdir()
        for index in range(81): (root/f'{index:03}.txt').write_text('fixture')
        quarantine = Quarantine(self.c, self.ops)
        page = quarantine.listing('untracked', 99)
        self.assertEqual(page['page'], 2); self.assertEqual(len(page['items']), 1)
        plan = self.preview(quarantine)
        self.assertEqual(plan['count'], 81); self.assertEqual(len(plan['files']), 80)
        self.assertEqual(quarantine.purge(plan['id'], True, Job('purge'))['count'], 81)

    def test_cancelling_multiple_batches_keeps_untouched_batches_restorable(self):
        self.batch(); self.batch('B.Look.1')
        quarantine = Quarantine(self.c, self.ops)
        plan = self.preview(quarantine)
        job = Job('purge')
        with mock.patch.object(job, 'update', side_effect=lambda *args:job.cancel.set()):
            with self.assertRaises(Cancelled): quarantine.purge(plan['id'], True, job)
        remaining = quarantine.listing()['batches']
        self.assertEqual(len(remaining), 1); self.assertTrue(remaining[0]['can_restore'])
        self.ops.undo(remaining[0]['id'], Job('undo'))

    def test_interrupted_dedup_is_restorable_but_cannot_be_permanently_cleaned(self):
        data = fixtures.var_bytes()
        for index in range(3): self.put(folder=str(index), data=data)
        self.scan(); self.c.duplicate_audit(Job('audit'))
        plan = self.ops.plan('dedup', [], Job('plan'))
        job = Job('dedup')
        with mock.patch.object(job, 'update', side_effect=lambda *args:job.cancel.set() if str(args[-1]).endswith('.var') else None):
            with self.assertRaises(Cancelled): self.ops.apply(plan['id'], job)
        quarantine = Quarantine(self.c, self.ops)
        self.assertTrue(quarantine.listing()['batches'][0]['can_restore'])
        with self.assertRaisesRegex(ValueError,'未完成'): self.preview(quarantine)
        self.ops.undo(plan['id'], Job('restore interrupted dedup'))
        self.assertEqual(quarantine.listing()['count'], 0)

    def test_purge_log_does_not_block_restoring_unrelated_collection_rename(self):
        self.put(folder='收藏'); self.scan()
        rename = self.ops.rename_collection('新收藏', Job('rename'))
        root = self.c.roots['quarantine']; root.mkdir()
        (root/'stray.txt').write_text('isolated')
        quarantine = Quarantine(self.c, self.ops)
        plan = self.preview(quarantine)
        quarantine.purge(plan['id'], True, Job('purge'))
        self.ops.undo(rename['operation'], Job('undo rename'))
        self.assertEqual(self.c.favorite_folder(), '收藏')
