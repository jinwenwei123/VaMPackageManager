import shutil
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.catalog import Catalog
from vampm.providers import AI
from vampm.util import Job


class SummaryStateTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def test_migration_preserves_old_text_but_initializes_once_as_not_generated(self):
        self.put('A.One.1');self.put('A.Two.1');self.scan()
        self.c.label('a.one.1',summary='旧 AI 简介',source='ai')
        self.c.label('a.two.1',summary='旧手动简介',favorite=True)
        with self.c.db() as db:
            labels=[tuple(r) for r in db.execute('SELECT * FROM labels ORDER BY pkgkey')]
            db.execute('DROP TABLE summary_state')
            db.execute("DELETE FROM settings WHERE key='summary_tracking_version'")
        self.c=Catalog(self.game,self.home)
        self.assertFalse(any(p['summary_generated'] for p in self.c.packages().values()))
        self.assertEqual(self.c.list({'state':'unlabeled'})['total'],2)
        with self.c.db() as db:self.assertEqual(labels,[tuple(r) for r in db.execute('SELECT * FROM labels ORDER BY pkgkey')])
        self.c.label('a.one.1',summary='新 AI 简介',source='ai')
        self.c=Catalog(self.game,self.home)
        self.assertTrue(self.c.detail('a.one.1')['summary_generated'])
        self.assertFalse(self.c.detail('a.two.1')['summary_generated'])
        self.put('A.New.1');self.scan()
        self.assertFalse(self.c.detail('a.new.1')['summary_generated'])

    def test_completion_survives_moves_recovery_and_external_folder_rename(self):
        original=self.put('A.Look.1');self.scan();self.c.label('a.look.1',summary='已完成简介',source='ai')
        plan=self.ops.plan('move',['a.look.1'],Job('plan'),folder='新目录')
        self.ops.apply(plan['id'],Job('move'));self.assertTrue(self.c.detail('a.look.1')['summary_generated'])
        self.ops.undo(plan['id'],Job('undo'));self.assertTrue(self.c.detail('a.look.1')['summary_generated'])
        new=original.parent/'外部移动'/original.name;new.parent.mkdir();original.rename(new);self.scan()
        self.assertTrue(self.c.detail('a.look.1')['summary_generated'])
        self.c=Catalog(self.game,self.home);self.assertTrue(self.c.detail('a.look.1')['summary_generated'])

    def test_changed_content_new_versions_and_new_variants_need_generation(self):
        original=self.put('A.Look.1');self.scan();self.c.label('a.look.1',summary='保留旧文字',source='ai')
        duplicate=original.parent/'副本'/original.name;duplicate.parent.mkdir();shutil.copy2(original,duplicate);self.scan()
        self.assertTrue(self.c.detail('a.look.1')['summary_generated'])
        duplicate.write_bytes(fixtures.var_bytes('A.Look.1',description='different contents in this copy'))
        self.put('A.Look.2');self.scan()
        self.assertFalse(self.c.detail('a.look.1')['summary_generated'])
        self.assertEqual(self.c.detail('a.look.1')['summary'],'保留旧文字')
        self.assertFalse(self.c.detail('a.look.2')['summary_generated'])

    def test_category_or_favorite_alone_does_not_complete_a_summary(self):
        self.put('A.Look.1');self.scan()
        self.c.label('a.look.1',category='人物',favorite=True)
        self.assertFalse(self.c.detail('a.look.1')['summary_generated'])
        self.c.label('a.look.1',summary='手动填写的完整简介')
        self.assertTrue(self.c.detail('a.look.1')['summary_generated'])
        self.c.label('a.look.1',summary='')
        self.assertFalse(self.c.detail('a.look.1')['summary_generated'])

    def test_failed_or_stale_analysis_cannot_mark_new_content_completed(self):
        file=self.put('A.Look.1');self.scan();ai=AI(self.c)
        def changed(items,job):
            file.write_bytes(fixtures.var_bytes('A.Look.1',description='changed while model was busy'))
            return {'a.look.1':{'category':'人物','summary':'已经过期的结果'}}
        with mock.patch.object(ai,'classify_batch',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'文件发生变化'):ai.generate(['a.look.1'],Job('ai'))
        self.scan();self.assertFalse(self.c.detail('a.look.1')['summary_generated'])
        self.assertNotEqual(self.c.detail('a.look.1')['summary'],'已经过期的结果')

    def test_label_and_completion_are_committed_atomically(self):
        self.put('A.Look.1');self.scan();before=self.c.detail('a.look.1')['summary']
        with mock.patch('vampm.catalog.SummaryState.save',side_effect=OSError('记录写入失败')):
            with self.assertRaises(OSError):self.c.label('a.look.1',summary='不应部分保存',source='ai')
        self.c=Catalog(self.game,self.home)
        self.assertEqual(self.c.detail('a.look.1')['summary'],before)
        self.assertFalse(self.c.detail('a.look.1')['summary_generated'])
