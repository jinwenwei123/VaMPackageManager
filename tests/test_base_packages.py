import json
import unittest
from unittest import mock

import test_manager as fixtures
from vampm.catalog import Catalog
from vampm.operations import Operations, move_no_replace
from vampm.util import Job, sha256


class BasePackageTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def execute(self,kind,keys=None,**kwargs):
        p=self.ops.plan(kind,keys or [],Job('preview'),**kwargs)
        self.ops.apply(p['id'],Job('apply'));return p

    def prepare(self):
        self.put('Base.Core.1','基础包',deps=['B.Tool.1'])
        self.put('B.Tool.1',deps=['C.Asset.1','Unavailable.Item.1'])
        self.put('C.Asset.1');self.put('Favorite.Scene.1','收藏')
        self.put('Other.Scene.1')
        self.scan();self.ops.ensure_default_profile()

    def test_existing_base_is_adopted_with_recursive_dependencies_and_one_copy(self):
        self.prepare();before={r['id']:sha256(self.c.path(r)) for r in self.c.rows()}
        old=self.c.packages()['base.core.1']['files'][0]['rel']
        self.execute('pin',['base.core.1']);plan=self.execute('stage_all')
        self.assertEqual(self.c.stats()['active'],3)
        self.assertEqual(self.c.stats()['pinned'],3)
        self.assertEqual(self.c.stats()['pin_roots'],1)
        self.assertEqual(self.c.packages()['base.core.1']['files'][0]['rel'],old)
        self.assertTrue(all(r['rel'].startswith('基础包/') for r in self.c.rows() if r['area']=='active'))
        self.assertEqual(before,{r['id']:sha256(self.c.path(r)) for r in self.c.rows()})
        self.assertEqual(self.c.list({'state':'pinned'})['total'],3)
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(self.c.stats()['active'],5)

    def test_favorite_and_organize_never_relocate_fixed_files(self):
        self.prepare();self.execute('pin',['base.core.1'])
        old={r['id']:(r['area'],r['rel']) for p in self.c.packages().values() if p['pinned'] for r in p['files']}
        self.ops.favorite(['base.core.1'],True,Job('star'))
        self.execute('organize',list(self.c.packages()))
        self.assertTrue(self.c.packages()['base.core.1']['favorite'])
        self.execute('activate',profile_id=self.ops.default_profile_id())
        self.ops.favorite(['base.core.1'],False,Job('unstar'))
        self.assertEqual(old,{r['id']:(r['area'],r['rel']) for p in self.c.packages().values() if p['pinned'] for r in p['files']})
        with self.assertRaisesRegex(ValueError,'解除固定'):
            self.ops.plan('move',['base.core.1'],Job('move'),folder='别处')

    def test_unpin_moves_released_closure_out_of_base_and_does_not_readopt(self):
        self.prepare();self.execute('pin',['base.core.1']);self.execute('stage_all')
        plan=self.execute('unpin',['base.core.1'])
        self.assertEqual(self.c.stats()['pinned'],0);self.assertEqual(self.c.stats()['active'],0)
        self.scan();fresh=Catalog(self.game,self.home)
        self.assertEqual(fresh.stats()['pinned'],0)
        self.assertTrue(all(not r['rel'].startswith('基础包/') for r in fresh.rows()))
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(self.c.stats()['active'],3);self.assertEqual(self.c.stats()['pinned'],3)

    def test_shared_base_dependency_remains_fixed_until_last_root_released(self):
        self.prepare();self.put('Second.Core.1',deps=['B.Tool.1']);self.scan()
        self.execute('pin',['base.core.1','second.core.1']);self.execute('stage_all')
        self.execute('unpin',['base.core.1'])
        self.assertEqual(self.c.stats()['active'],3)
        self.assertTrue(self.c.packages()['b.tool.1']['pinned'])
        self.assertEqual(self.c.packages()['b.tool.1']['pin_parent'],'second.core.1')
        with self.assertRaisesRegex(ValueError,'上游'):
            self.ops.plan('unpin',['b.tool.1'],Job('unpin'))

    def test_fixed_duplicates_are_not_selected_or_cleaned_or_renamed(self):
        self.prepare();self.put('Base.Core.1','another',description='other variant')
        self.put('B.Tool.1','copy',deps=['C.Asset.1','Unavailable.Item.1'])
        self.scan();self.execute('pin',['base.core.1'])
        before={r['id']:(r['rel'],sha256(self.c.path(r))) for r in self.c.rows() if r['rel'].startswith('基础包/')}
        plan=self.execute('activate',['base.core.1'])
        self.assertEqual(plan['unresolved_copies'],[])
        self.c.duplicate_audit(Job('audit'));self.execute('dedup')
        self.assertEqual(before,{r['id']:(r['rel'],sha256(self.c.path(r))) for r in self.c.rows() if r['rel'].startswith('基础包/')})
        self.assertTrue(all(r['area']=='active' for p in self.c.packages().values() if p['pinned'] for r in p['files']))

    def test_new_dependency_sync_and_unpin_restores_under_current_profile(self):
        self.prepare();self.execute('pin',['base.core.1']);self.execute('activate',profile_id=self.ops.default_profile_id())
        self.put('Unavailable.Item.1');self.scan();self.ops.sync_base(Job('sync'))
        self.assertTrue(self.c.packages()['unavailable.item.1']['pinned'])
        self.assertEqual(self.c.packages()['unavailable.item.1']['files'][0]['rel'],'基础包/Unavailable.Item.1.var')
        self.execute('unpin',['base.core.1'])
        self.assertEqual(self.c.stats()['active'],1)

    def test_pin_interruption_undo_preserves_all_original_bytes(self):
        self.prepare();old={r['id']:(r['area'],r['rel'],sha256(self.c.path(r))) for r in self.c.rows()}
        plan=self.ops.plan('pin',['base.core.1'],Job('preview'));i=0
        def fail(src,dst,job=None):
            nonlocal i
            i+=1
            if i==2:raise OSError('interrupted')
            move_no_replace(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail):
            with self.assertRaises(OSError):self.ops.apply(plan['id'],Job('apply'))
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(old,{r['id']:(r['area'],r['rel'],sha256(self.c.path(r))) for r in self.c.rows()})

    def test_stale_pin_settings_and_old_undo_cannot_move_newly_fixed_package(self):
        self.prepare();self.execute('pin',['base.core.1']);old=self.execute('stage_all')
        preview=self.ops.plan('activate',['other.scene.1'],Job('preview'))
        self.execute('pin',['other.scene.1'])
        with self.assertRaisesRegex(ValueError,'变化'):self.ops.apply(preview['id'],Job('apply'))
        with self.assertRaisesRegex(ValueError,'固定'):self.ops.undo(old['id'],Job('undo'))

    def test_base_attachments_stay_put_during_stage_all(self):
        self.prepare();path=self.game/'AddonPackages/基础包/说明.txt';path.write_bytes(b'base docs')
        self.execute('stage_all')
        self.assertEqual(path.read_bytes(),b'base docs')


if __name__=='__main__':unittest.main()
