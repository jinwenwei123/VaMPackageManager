import io
import json
import unittest
from unittest import mock

import test_manager as fixtures
from test_manager import var_bytes
from vampm.importer import Importer
from vampm.providers import AI, Downloads
from vampm.util import Job, Jobs, Cancelled
from vampm.operations import move_no_replace


class PerformanceInvariants(unittest.TestCase):
    setUp = fixtures.Workspace.setUp
    tearDown = fixtures.Workspace.tearDown
    put = fixtures.Workspace.put
    scan = fixtures.Workspace.scan


    def test_move_preserves_deep_refs_and_hashes(self):
        self.put(files={'Saves/scene/test.json':'{"ref":"Unlisted.Hair.7:/hair"}'})
        self.scan(deep=True)
        before=self.c.detail('author.scene.1')
        plan=self.ops.plan('organize',['author.scene.1'],Job('plan'))
        with mock.patch('vampm.catalog.inspect_var',side_effect=AssertionError('No archive reread on relocation')):
            self.ops.apply(plan['id'],Job('apply'))
            self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(self.c.detail('author.scene.1')['deps'],before['deps'])
        self.assertEqual(self.c.detail('author.scene.1')['deep'],1)

    def test_cached_queries_invalidate_after_label_and_scan(self):
        self.put();self.scan()
        self.assertEqual(self.c.list({'state':'favorite'})['total'],0)
        self.c.label('author.scene.1',favorite=True,summary='缓存检验',category='人物')
        self.assertEqual(self.c.list({'q':'缓存检验'})['total'],1)
        self.assertEqual(self.c.list({'state':'favorite'})['total'],1)
        self.assertEqual(self.c.stats()['categories']['人物'],1)
        self.put('B.Other.1');self.scan()
        self.assertEqual(self.c.list({})['total'],2)

    def test_import_only_indexes_added_files(self):
        self.put();self.scan()
        imp=Importer(self.c,AI(self.c),Downloads(self.c))
        data=var_bytes('New.Hair.1',files={'Saves/scene/test.json':'"Hidden.Ref.3:/x"'})
        upload=imp.upload(io.BytesIO(data),'New.Hair.1.var',len(data))
        with mock.patch.object(self.c,'scan',side_effect=AssertionError('Import must not rescan whole library')):
            imp.run([upload['id']],Job('import'),False,False)
        self.assertEqual(self.c.stats()['files'],2)
        self.assertIn('Hidden.Ref.3',self.c.detail('new.hair.1')['deps'])

    def test_quarantine_metadata_hidden_and_undo_retained(self):
        import shutil
        a=self.put();b=a.parent/'copy'/a.name;b.parent.mkdir();shutil.copy2(a,b)
        self.scan(deep=True);self.c.duplicate_audit(Job('audit'))
        p=self.ops.plan('dedup',[],Job('plan'));self.ops.apply(p['id'],Job('apply'))
        self.assertEqual(self.c.stats()['files'],1)
        with mock.patch('vampm.catalog.inspect_var',side_effect=AssertionError('Undo should reuse quarantine metadata')):
            self.ops.undo(p['id'],Job('undo'))
        self.assertEqual(self.c.stats()['files'],2)
        self.assertTrue(all(json.loads(r['info'])['deep'] for r in self.c.rows()))

    def test_cancel_inside_write_ahead_batch_restores_only_moved_files(self):
        for i in range(70): self.put(f'Fixture.Item{i}.1')
        self.scan(deep=True)
        p=self.ops.plan('organize',list(self.c.packages()),Job('plan'))
        job=Job('apply');count=[0]
        def cancel_after_two(src,dst,job=None):
            move_no_replace(src,dst);count[0]+=1
            if count[0]==2:job.cancel.set()
        with mock.patch('vampm.operations.move_no_replace',side_effect=cancel_after_two):
            with self.assertRaises(Cancelled):self.ops.apply(p['id'],job)
        events=(self.ops.directory/p['id']/'events.jsonl').read_text('utf-8').splitlines()
        self.assertEqual(sum(json.loads(x)['phase']=='intent' for x in events),64)
        self.assertEqual(self.ops.undo(p['id'],Job('restore'))['restored'],2)
        self.assertEqual(self.c.stats()['files'],70)
        self.assertTrue(all('/' not in r['rel'] for r in self.c.rows()))

    def test_progress_does_not_return_large_results(self):
        jobs=Jobs();j=Job('large');j.result={'items':['x'*1000]*1000};j.status='done';jobs.jobs[j.id]=j
        self.assertNotIn('result',jobs.all()[0])
        self.assertIn('result',j.public())
