"""End-to-end filesystem invariants for scanning, grouping and applied modes."""
import io
import shutil
from types import SimpleNamespace
from unittest import TestCase, mock

import test_manager as fixtures
from vampm.importer import Importer
from vampm.operations import move_no_replace
from vampm.providers import AI, Downloads
from vampm.server import Application
from vampm.util import Job, sha256


class ActivationSyncTests(TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def execute(self,kind,keys=None,**kwargs):
        plan=self.ops.plan(kind,keys or [],Job('preview'),**kwargs)
        return self.ops.apply(plan['id'],Job('apply'))

    def refresh(self):
        app=SimpleNamespace(c=self.c,ops=self.ops)
        app.sync_library=lambda result,job:Application.sync_library(app,result,job)
        return Application.scan(app,Job('UI scan'))

    def location(self,key):
        row=self.c.packages()[key]['files'][0]
        return row['area'],row['rel']

    def test_scan_without_applied_profile_stages_manual_files_and_keeps_base(self):
        self.put('Base.Core.1','基础包',deps=['Base.Helper.1'])
        self.put('Base.Helper.1')
        original=self.put('New.Look.1','下载',files={'Custom/Atom/Person/Appearance/a.vap':'{}'})
        digest=sha256(original)
        self.scan();self.ops.ensure_default_profile()  # Saved does not mean applied.
        self.refresh()
        self.assertEqual(self.location('new.look.1'),('vault','人物/New.Look.1.var'))
        self.assertEqual(self.location('base.helper.1'),('active','基础包/Base.Helper.1.var'))
        self.assertTrue(self.c.packages()['base.core.1']['active'])
        self.assertEqual(sha256(self.c.path(self.c.packages()['new.look.1']['files'][0])),digest)
        self.assertFalse(original.exists())
        self.assertEqual(self.refresh()['activation_sync']['count'],0)
        self.assertFalse(self.ops.plans)

    def test_organize_alone_enforces_stopped_mode_and_preserves_group_and_summary(self):
        original=self.put('New.Look.1','下载/New.Look.1',files={'Custom/Atom/Person/Appearance/a.vap':'{}'})
        (original.parent/'说明.txt').write_bytes(b'notes')
        (original.parent/'cover.png').write_bytes(b'picture')
        self.scan();self.c.label('new.look.1',summary='已保存的简介',source='ai')
        result=self.execute('organize',['new.look.1'])
        target=self.c.roots['vault']/'人物/New.Look.1'
        self.assertEqual((target/'说明.txt').read_bytes(),b'notes')
        self.assertEqual((target/'cover.png').read_bytes(),b'picture')
        self.assertTrue((target/'New.Look.1.var').is_file())
        self.assertEqual(len(self.c.rows()),1)
        p=self.c.packages()['new.look.1']
        self.assertEqual(p['summary'],'已保存的简介');self.assertTrue(p['summary_generated'])
        self.ops.undo(result['operation'],Job('undo'))
        self.assertTrue(original.exists());self.assertTrue((original.parent/'说明.txt').exists())

    def test_favorite_without_mode_stays_vault_then_follows_default_and_dependencies(self):
        self.put('A.Look.1',deps=['B.Hair.1','Missing.Resource.1'])
        self.put('B.Hair.1',files={'Custom/Hair/Female/b.vam':'{}'})
        self.scan();pid=self.ops.ensure_default_profile()
        self.ops.favorite(['a.look.1'],True,Job('favorite'))
        self.assertEqual(self.location('a.look.1'),('vault','收藏/场景/A.Look.1.var'))
        self.execute('activate',profile_id=pid)
        self.assertEqual(self.location('a.look.1'),('active','收藏/场景/A.Look.1.var'))
        self.assertEqual(self.location('b.hair.1'),('active','头发/B.Hair.1.var'))
        self.put('C.New.1',deps=['B.Hair.1']);self.refresh()
        self.assertEqual(self.location('c.new.1'),('vault','场景/C.New.1.var'))
        self.ops.favorite(['c.new.1'],True,Job('favorite'))
        self.assertEqual(self.location('c.new.1'),('active','收藏/场景/C.New.1.var'))
        self.ops.favorite(['a.look.1'],False,Job('unfavorite'))
        self.assertEqual(self.location('a.look.1'),('vault','场景/A.Look.1.var'))
        self.assertTrue(self.c.packages()['b.hair.1']['active'])

    def test_custom_profile_scan_enables_new_missing_dependency_not_unrelated_package(self):
        self.put('A.Scene.1',deps=['New.Hair.1']);self.scan()
        profile=self.ops.save_profile('指定场景',['a.scene.1'])[0]
        self.execute('activate',profile_id=profile['id'])
        self.put('New.Hair.1','手动添加',files={'Custom/Hair/Female/h.vam':'{}'})
        self.put('Other.Look.1');self.refresh()
        self.assertTrue(self.c.packages()['new.hair.1']['active'])
        self.assertEqual(self.location('other.look.1'),('vault','场景/Other.Look.1.var'))
        self.ops.favorite(['other.look.1'],True,Job('star'))
        self.assertEqual(self.location('other.look.1'),('vault','收藏/场景/Other.Look.1.var'))
        self.execute('organize',['new.hair.1'])
        self.assertEqual(self.location('new.hair.1'),('active','头发/New.Hair.1.var'))

    def test_folder_profile_retains_enrolled_members_after_classification_and_new_scan(self):
        self.put('A.Scene.1','手选');self.scan()
        profile=self.ops.save_profile('文件夹',[],folder='手选')[0]
        self.execute('activate',profile_id=profile['id'])
        self.assertEqual(self.location('a.scene.1'),('active','场景/A.Scene.1.var'))
        self.refresh();self.assertTrue(self.c.packages()['a.scene.1']['active'])
        self.put('B.New.1','手选');self.refresh()
        self.execute('organize',['b.new.1']);self.refresh()
        self.assertEqual(self.location('b.new.1'),('active','场景/B.New.1.var'))
        self.assertEqual(self.ops.profiles()[0]['keys'],['a.scene.1','b.new.1'])
        self.execute('stage_all');self.execute('activate',profile_id=profile['id']);self.refresh()
        self.assertEqual(self.c.stats()['active'],2)

    def test_temporary_copy_choice_is_used_by_scan_even_without_saving(self):
        first=self.put('A.Scene.1','第一份',description='first')
        self.put('A.Scene.1','第二份',description='second');self.scan()
        selected=sha256(first)
        self.execute('activate',['a.scene.1'],duplicate_mode='select',copy_choices={'a.scene.1':selected})
        self.refresh()
        rows=[r for r in self.c.rows() if r['area']=='active']
        self.assertEqual(len(rows),1);self.assertEqual(sha256(self.c.path(rows[0])),selected)
        self.assertEqual(self.ops.activation.policy()['choices'],{})
        self.ops.plan('activate',['a.scene.1'],Job('unapplied preview'),duplicate_mode='all',remember_copies=True)
        self.refresh()
        self.assertEqual(len([r for r in self.c.rows() if r['area']=='active']),1)

    def test_explicit_restore_all_survives_scans_and_imports(self):
        self.put();self.execute('restore_all')
        self.put('B.New.1');self.refresh()
        self.assertTrue(self.c.packages()['b.new.1']['active'])
        self.assertEqual(self.ops.import_target()['area'],'active')
        self.execute('organize',['b.new.1'])
        self.assertEqual(self.location('b.new.1'),('active','场景/B.New.1.var'))

    def test_preview_rejects_mode_change_even_if_files_did_not_move(self):
        self.put(folder='场景');self.scan()
        plan=self.ops.plan('organize',['author.scene.1'],Job('preview'))
        self.execute('restore_all')
        with self.assertRaisesRegex(ValueError,'启动方案'):
            self.ops.apply(plan['id'],Job('stale'))
        self.assertTrue(self.c.packages()['author.scene.1']['active'])

    def test_deleted_applied_profile_falls_back_to_no_selection(self):
        self.put();self.scan();p=self.ops.save_profile('临时',['author.scene.1'])[0]
        self.execute('activate',profile_id=p['id']);self.ops.delete_profile(p['id'])
        self.refresh();self.assertFalse(self.c.packages()['author.scene.1']['active'])

    def test_import_defaults_to_vault_and_api_sync_activates_new_needed_dependency(self):
        imp=Importer(self.c,AI(self.c),Downloads(self.c),self.ops)
        data=fixtures.var_bytes('A.Scene.1',deps=['B.Hair.1'])
        uid=imp.upload(io.BytesIO(data),'A.Scene.1.var',len(data))['id']
        result=imp.run([uid],Job('import'),False,False)
        self.assertEqual(result['area'],'vault')
        self.execute('activate',['a.scene.1'])
        data=fixtures.var_bytes('B.Hair.1',files={'Custom/Hair/Female/h.vam':'{}'})
        uid=imp.upload(io.BytesIO(data),'B.Hair.1.var',len(data))['id']
        result=imp.run([uid],Job('import'),False,False)
        Application.sync_library(SimpleNamespace(c=self.c,ops=self.ops),result,Job('sync'))
        self.assertEqual(self.location('b.hair.1'),('active','头发/B.Hair.1.var'))

    def test_interrupted_scan_moves_recover_all_original_bytes(self):
        self.put('A.Scene.1');self.put('B.Look.1');self.scan()
        before={str(self.c.path(r)):sha256(self.c.path(r)) for r in self.c.rows()}
        calls=0
        def fail(src,dst,job=None):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('interrupted sync')
            move_no_replace(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail):
            with self.assertRaises(OSError):self.refresh()
        pending=next(h for h in self.ops.history() if h['status']=='interrupted')
        self.ops.undo(pending['id'],Job('recover'))
        self.assertEqual(before,{str(self.c.path(r)):sha256(self.c.path(r)) for r in self.c.rows()})

    def test_deleted_group_does_not_capture_reused_file_id(self):
        old=self.put('Old.Look.1','Old.Look.1');(old.parent/'old.txt').write_bytes(b'old')
        self.refresh();row=self.c.packages()['old.look.1']['files'][0];old_id=row['id']
        shutil.rmtree(self.c.path(row).parent);self.scan()
        new=self.put('New.Look.1','手动下载/New.Look.1');(new.parent/'说明.txt').write_bytes(b'new notes')
        self.scan();self.assertEqual(self.c.packages()['new.look.1']['files'][0]['id'],old_id)
        self.refresh()
        self.assertEqual(self.location('new.look.1'),('vault','场景/New.Look.1/New.Look.1.var'))
        self.assertEqual((self.c.roots['vault']/'场景/New.Look.1/说明.txt').read_bytes(),b'new notes')
        self.assertEqual([g['owner'] for g in self.ops.bundles.state()['groups']],['new.look.1'])
