import copy
import json
from types import SimpleNamespace
from unittest import TestCase, mock

import test_manager as fixtures
from vampm.name_repair import NameRepairs, NAME_ERROR, valid_filename
from vampm.operations import move_no_replace
from vampm.util import Job, atomic_json, read_json, sha256


class NameRepairTests(TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def bad(self,name='A.Scene.2_2',folder='',canonical='A.Scene.2',**kw):
        return self.put(name,folder,data=fixtures.var_bytes(canonical,**kw))

    def service(self,names=None):
        calls=[]
        def complete(cfg,instruction,items,schema,job,model):
            self.assertIn('"items"',instruction)
            calls.append((model,copy.deepcopy(items)))
            return {'items':[{'id':x['id'],'filename':(names or {}).get(x['filename'],'A.Scene.2.var'),
                              'reason':'包内身份与原版本对应，移除下载重名后缀。','confidence':'high'} for x in items]}
        ai=SimpleNamespace(settings=lambda private:{'provider':'deepseek','api_model':'chat-model','api_summary_model':'summary-model'},complete_json=complete)
        service=NameRepairs(self.c,self.ops,ai);service.calls=calls;return service

    def analyze(self,service):return service.analyze(Job('AI preview'))
    def apply(self,service,preview):return service.apply(preview['id'],[{'id':x['id'],'filename':x['filename']} for x in preview['items']],Job('repair'))

    def test_only_filename_errors_are_eligible_and_summary_model_is_used(self):
        self.bad()
        self.put('Bad.Archive.latest',data=b'bad')
        self.put('Good.Look.1');self.scan();service=self.service()
        self.assertEqual(service.status()['eligible'],1);self.assertEqual(service.status()['skipped'],1)
        preview=self.analyze(service)
        self.assertEqual(preview['model'],'summary-model');self.assertEqual(service.calls[0][0],'summary-model')
        self.assertEqual(service.calls[0][1][0]['packageName'],'Scene')
        self.assertTrue(self.c.packages()['a.scene.2_2']['error']);self.assertFalse(list(self.ops.directory.glob('*/manifest.json')))

    def test_rename_preserves_bytes_identity_summary_favorites_profiles_and_undo(self):
        original=self.bad(folder='收藏/场景');self.scan()
        row=self.c.packages()['a.scene.2_2']['files'][0];rid=row['id'];digest=sha256(original)
        self.c.label('a.scene.2_2',category='场景',summary='手工说明',source='manual',tags=['人物展示','写实'])
        profile=self.ops.save_profile('原始选择',['a.scene.2_2'])[0]
        service=self.service();result=self.apply(service,self.analyze(service))
        p=self.c.packages()['a.scene.2'];self.assertFalse(original.exists())
        self.assertFalse(p['error']);self.assertTrue(p['favorite']);self.assertTrue(p['summary_generated'])
        self.assertEqual(p['tags'],['人物展示','写实']);self.assertTrue(p['tags_generated'])
        self.assertEqual(p['summary'],'手工说明');self.assertEqual(p['files'][0]['id'],rid)
        self.assertEqual(sha256(self.c.path(p['files'][0])),digest)
        self.assertEqual(self.ops.profiles()[0]['keys'],['a.scene.2'])
        history=next(x for x in self.ops.history() if x['id']==result['operation'])
        self.assertNotIn('name_repair_state',history)
        self.ops.undo(result['operation'],Job('undo'))
        self.assertTrue(original.exists());self.assertEqual(sha256(original),digest)
        self.assertEqual(self.ops.profiles()[0]['id'],profile['id']);self.assertEqual(self.ops.profiles()[0]['keys'],['a.scene.2_2'])
        self.assertEqual(self.c.packages()['a.scene.2_2']['error'],NAME_ERROR)
        self.assertEqual(self.c.detail('a.scene.2_2')['tags'],['人物展示','写实']);self.assertTrue(self.c.detail('a.scene.2_2')['tags_generated'])

    def test_same_name_is_preserved_in_another_directory_with_merged_favorite(self):
        original=self.bad();existing=self.put('A.Scene.2',description='different content');self.scan()
        oldhash=sha256(original);existinghash=sha256(existing)
        self.c.label('a.scene.2_2',summary='旧名说明',favorite=True)
        self.c.label('a.scene.2',summary='已有说明',source='manual')
        self.ops.activation.save({'mode':'select','choices':{'a.scene.2':existinghash,'a.scene.2_2':oldhash}})
        service=self.service();preview=self.analyze(service);self.assertTrue(preview['items'][0]['conflict'])
        result=self.apply(service,preview);p=self.c.packages()['a.scene.2']
        self.assertEqual(p['copies'],2);self.assertTrue(p['favorite']);self.assertEqual(p['summary'],'已有说明')
        self.assertTrue(existing.exists());self.assertEqual(sha256(existing),existinghash)
        self.assertEqual({sha256(self.c.path(r)) for r in p['files']},{oldhash,existinghash})
        self.assertTrue(any('修复副本-' in r['rel'] for r in p['files']))
        self.assertEqual(self.ops.activation.policy()['choices'],{'a.scene.2':existinghash})
        self.ops.undo(result['operation'],Job('restore'))
        self.assertEqual(sha256(original),oldhash);self.assertEqual(sha256(existing),existinghash)
        self.assertEqual(self.ops.activation.policy()['choices'],{'a.scene.2':existinghash,'a.scene.2_2':oldhash})

    def test_fixed_base_key_is_migrated_without_changing_enabled_area(self):
        self.bad(folder='基础包');self.scan();self.ops.base.save({'roots':['a.scene.2_2']})
        service=self.service();result=self.apply(service,self.analyze(service))
        self.assertEqual(self.ops.base.state()['roots'],['a.scene.2'])
        p=self.c.packages()['a.scene.2'];self.assertTrue(p['pinned']);self.assertTrue(p['active'])
        self.assertEqual(p['files'][0]['rel'],'基础包/A.Scene.2.var')
        self.ops.undo(result['operation'],Job('undo'));self.assertEqual(self.ops.base.state()['roots'],['a.scene.2_2'])

    def test_applied_temporary_selection_uses_alias_after_repair(self):
        self.bad();self.put('Good.Root.1');self.scan()
        plan=self.ops.plan('activate',['good.root.1','a.scene.2_2'],Job('preview'))
        self.ops.apply(plan['id'],Job('activate'))
        service=self.service();self.apply(service,self.analyze(service))
        self.ops.sync_activation(Job('sync'))
        self.assertTrue(self.c.packages()['a.scene.2']['active'])
        self.assertTrue(self.c.packages()['good.root.1']['active'])

    def test_resource_group_owner_and_member_name_follow_repair(self):
        path=self.bad(folder='下载/A.Scene.2_2');(path.parent/'说明.txt').write_bytes(b'notes');self.scan()
        self.ops.bundles.save(self.ops.bundles.discover(self.c.rows(),[
            {'id':None,'area':'active','rel':'下载/A.Scene.2_2/说明.txt','size':5,'mtime':(path.parent/'说明.txt').stat().st_mtime_ns,'auxiliary':True}]))
        service=self.service();result=self.apply(service,self.analyze(service))
        g=self.ops.bundles.state()['groups'][0]
        self.assertEqual(g['owner'],'a.scene.2');self.assertEqual(list(g['members'].values()),['A.Scene.2.var'])
        self.ops.sync_activation(Job('scan'))
        target=self.c.roots['vault']/'场景/A.Scene.2'
        self.assertTrue((target/'A.Scene.2.var').exists());self.assertEqual((target/'说明.txt').read_bytes(),b'notes')
        sync=next(h for h in self.ops.history() if h['kind']=='sync_activation')
        self.ops.undo(sync['id'],Job('undo sync'));self.ops.undo(result['operation'],Job('undo repair'))
        self.assertTrue(path.exists());self.assertTrue((path.parent/'说明.txt').exists())

    def test_ai_and_edited_names_are_validated_and_cannot_escape_library(self):
        path=self.bad();self.scan();service=self.service({'A.Scene.2_2.var':'../A.Scene.2.var'})
        with self.assertRaisesRegex(ValueError,'格式'):self.analyze(service)
        service=self.service();preview=self.analyze(service)
        for name in ('../A.Scene.2.var','A.Scene.latest.var','A.Scene.1_2.var','A.B.C.1.var','NUL.Test.1.var'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):service.apply(preview['id'],[{'id':preview['items'][0]['id'],'filename':name}],Job('bad'))
        self.assertTrue(path.exists());self.assertFalse(list(self.ops.directory.glob('*/manifest.json')))

    def test_preview_rejects_file_changes_and_unrelated_ids(self):
        path=self.bad();self.scan();service=self.service();preview=self.analyze(service)
        with self.assertRaises(ValueError):service.apply(preview['id'],[{'id':999,'filename':'A.Scene.2.var'}],Job('bad id'))
        path.write_bytes(fixtures.var_bytes('A.Scene.2',description='changed'))
        with self.assertRaisesRegex(ValueError,'文件库已变化'):self.apply(service,preview)

    def test_interrupted_rename_recovers_even_after_filesystem_rename_but_before_counter(self):
        original=self.bad();self.scan();service=self.service();preview=self.analyze(service);digest=sha256(original)
        def fail_after(src,dst):move_no_replace(src,dst);raise OSError('crash after rename')
        with mock.patch('vampm.name_repair.move_no_replace',side_effect=fail_after):
            with self.assertRaises(OSError):self.apply(service,preview)
        record=next(x for x in self.ops.history() if x['kind']=='repair_names')
        self.assertEqual(record['status'],'interrupted');self.assertFalse(original.exists())
        self.scan();self.ops.undo(record['id'],Job('recover'))
        self.assertEqual(sha256(original),digest);self.assertTrue(self.c.packages()['a.scene.2_2']['error'])

    def test_database_and_config_partial_failure_can_be_rolled_back(self):
        original=self.bad();self.scan();self.ops.save_profile('方案',['a.scene.2_2'])
        service=self.service();preview=self.analyze(service);real=atomic_json
        def fail(path,data):
            if path.name=='profiles.json':raise OSError('config failure')
            real(path,data)
        with mock.patch('vampm.name_repair.atomic_json',side_effect=fail):
            with self.assertRaises(OSError):self.apply(service,preview)
        record=next(x for x in self.ops.history() if x['kind']=='repair_names')
        self.ops.undo(record['id'],Job('recover'))
        self.assertTrue(original.exists());self.assertEqual(self.ops.profiles()[0]['keys'],['a.scene.2_2'])
        self.assertIsNone(read_json(self.c.data/'package-key-aliases.json'))

    def test_undo_refuses_changed_content_or_newer_configuration(self):
        self.bad();self.scan();service=self.service();result=self.apply(service,self.analyze(service))
        atomic_json(self.c.data/'base-packages.json',{'roots':['other.package.1']})
        with self.assertRaisesRegex(ValueError,'配置已变化'):self.ops.undo(result['operation'],Job('undo'))

    def test_filename_validation_limits_version_and_windows_names(self):
        self.assertTrue(valid_filename('Sample.Asset.2.var'))
        self.assertTrue(valid_filename('LocalRepair.人物.1.var'))
        self.assertFalse(valid_filename('Sample.Asset.2147483648.var'))
        self.assertFalse(valid_filename('作者 .场景.1.var'))

    def test_codex_also_uses_summary_model(self):
        self.bad();self.scan();service=self.service()
        service.ai.settings=lambda private:{'provider':'codex','codex_model':'chat-model','codex_summary_model':'codex-summary'}
        self.assertEqual(self.analyze(service)['model'],'codex-summary')
        self.assertEqual(service.calls[0][0],'codex-summary')

    def test_failed_second_batch_resumes_without_repeating_finished_analysis(self):
        for i in range(6):self.bad('A.Scene'+str(i)+'.2_2',canonical='A.Scene'+str(i)+'.2')
        self.scan();service=self.service();complete=service.ai.complete_json;calls=0
        def fail_second(*args):
            nonlocal calls
            calls+=1
            if calls==2:raise ConnectionResetError('connection reset')
            return complete(*args)
        service.ai.complete_json=fail_second
        with self.assertRaises(ConnectionResetError):self.analyze(service)
        self.assertEqual(service.status()['analyzed'],5);self.assertFalse(service.status()['preview_ready'])
        with self.assertRaises(ValueError):service.last_preview()
        service.ai.complete_json=complete
        result=self.analyze(service)
        self.assertEqual(len(result['items']),6);self.assertEqual(len(service.calls),2)
        self.assertEqual(len(service.calls[-1][1]),1)
        self.assertTrue(service.status()['preview_ready'])
