import io
import json
import os
import threading
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import test_manager as fixtures

var_bytes=fixtures.var_bytes
from vampm.importer import Importer
from vampm.operations import move_no_replace
from vampm.providers import AI, Downloads
from vampm.server import Application, serve
from vampm.util import Cancelled, Job, sha256


class FolderImportTests(unittest.TestCase):
    setUp=fixtures.Workspace.setUp
    tearDown=fixtures.Workspace.tearDown
    put=fixtures.Workspace.put
    scan=fixtures.Workspace.scan

    def importer(self):
        return Importer(self.c,AI(self.c),Downloads(self.c),self.ops)

    def upload(self,imp,relative,data):
        return imp.upload(io.BytesIO(data),relative.rsplit('/',1)[-1],len(data),relative)['id']

    def apply(self,kind='activate',keys=None):
        plan=self.ops.plan(kind,keys or ['author.scene.1'],Job('plan'))
        self.ops.apply(plan['id'],Job('apply'))
        return plan

    def test_folder_preserves_nested_attachments_names_and_source(self):
        imp=self.importer()
        original=self.root/'Original.Scene.1.var';original.write_bytes(var_bytes('Original.Scene.1'))
        contents={'人物合集/子目录/Original.Scene.1.var':original.read_bytes(),
                  '人物合集/封面.PNG':b'picture','人物合集/说明/依赖.txt':'缺少的资源'.encode(),
                  '人物合集/说明/空白.txt':b'','人物合集/说明/参数.json':b'{}'}
        ids=[self.upload(imp,p,b) for p,b in contents.items()]
        result=imp.run(ids,Job('import'),False,False)
        self.assertEqual((result['installed'],result['attachments'],result['failed']),(1,4,0))
        for rel,data in contents.items():
            self.assertEqual((self.c.roots['vault']/'包管家导入'/rel).read_bytes(),data)
        self.assertEqual(original.read_bytes(),contents['人物合集/子目录/Original.Scene.1.var'])
        self.assertEqual(self.c.stats()['packages'],1)
        self.assertFalse(self.c.packages()['original.scene.1']['summary_generated'])
        self.assertFalse(list(imp.staging.iterdir()))

    def test_on_demand_import_does_not_expand_active_set_and_restores_mode(self):
        self.put(folder='场景');self.scan();imp=self.importer()
        # Even activation with zero physical moves is still an applied profile.
        plan=self.apply();self.assertEqual(plan['count'],0)
        self.assertEqual(self.ops.import_target()['area'],'vault')
        data=var_bytes('New.Look.1',deps=['Missing.Hair.1'])
        ids=[self.upload(imp,'新人物/New.Look.1.var',data),self.upload(imp,'新人物/说明.txt',b'doc')]
        result=imp.run(ids,Job('import'),False,False)
        self.assertEqual((result['area'],result['missing']),('vault',1))
        self.assertEqual(self.c.stats()['active'],1)
        self.assertFalse(self.c.packages()['new.look.1']['active'])
        restored=self.apply('restore_all')
        self.assertEqual(self.ops.import_target()['area'],'active')
        # Restore-all transfers attachments together with the whole library.
        self.assertTrue((self.c.roots['active']/'场景/New.Look.1/说明.txt').exists())
        self.ops.undo(restored['id'],Job('undo'))
        self.assertTrue((self.c.roots['vault']/'包管家导入/新人物/说明.txt').exists())
        self.assertEqual(self.ops.import_target()['area'],'vault')
        self.ops.undo(plan['id'],Job('undo'))
        # Without a profile record, existing vault VARs still select the vault.
        self.assertEqual(self.ops.import_target()['area'],'vault')

    def test_undo_zero_move_activation_returns_to_default_disabled_imports(self):
        self.put(folder='场景');self.scan();plan=self.apply()
        self.assertEqual(plan['count'],0)
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(self.ops.import_target()['area'],'vault')

    def test_rewriting_an_old_manifest_does_not_change_import_mode(self):
        self.put();self.scan();activated=self.apply()
        self.apply('restore_all')
        manifest=self.ops.directory/activated['id']/'manifest.json'
        st=manifest.stat();os.utime(manifest,ns=(st.st_atime_ns,st.st_mtime_ns+100000000000))
        self.assertEqual(self.ops.import_target()['area'],'active')

    def test_applied_mode_is_checked_after_upload(self):
        self.put();self.scan();imp=self.importer()
        uid=self.upload(imp,'选好的目录/New.Look.1.var',var_bytes('New.Look.1'))
        self.apply()
        result=imp.run([uid],Job('import'),False,False)
        self.assertEqual(result['area'],'vault')
        self.assertEqual(self.c.stats()['active'],1)

    def test_existing_content_skipped_but_sidecars_preserved_in_new_folder(self):
        imp=self.importer();data=var_bytes()
        contents={'人物/Author.Scene.1.var':data,'人物/封面.jpg':b'first'}
        imp.run([self.upload(imp,p,b) for p,b in contents.items()],Job('first'),False,False)
        self.apply()
        result=imp.run([self.upload(imp,'人物/Author.Scene.1.var',data),
                        self.upload(imp,'人物/封面.jpg',b'second')],Job('second'),False,False)
        self.assertEqual((result['skipped'],result['attachments'],result['installed']),(1,1,0))
        # A category-wide loose image has no unique owner; do not attach it to
        # the first VAR just because both were imported together.
        self.assertEqual((self.c.roots['vault']/'包管家导入/人物/封面.jpg').read_bytes(),b'first')
        self.assertEqual((self.c.roots['vault']/'包管家导入/人物（2）/封面.jpg').read_bytes(),b'second')

    def test_different_content_keeps_uid_and_existing_manual_summary(self):
        original=self.put(description='original');self.scan();before=sha256(original)
        self.c.label('author.scene.1','场景','我的简介',source='manual')
        imp=self.importer()
        variants=[var_bytes(description=d) for d in ['variant1','variant2']]
        ids=[self.upload(imp,'合集/'+str(i)+'/Author.Scene.1.var',b) for i,b in enumerate(variants)]
        with mock.patch.object(imp.ai,'classify_batch') as ai:
            result=imp.run(ids,Job('import'),True,False)
        ai.assert_not_called()
        self.assertEqual((result['installed'],result['failed']),(2,0))
        self.assertEqual(sha256(original),before)
        package=self.c.packages()['author.scene.1']
        self.assertEqual(package['summary'],'我的简介')
        self.assertFalse(package['summary_generated'])
        self.assertEqual(len(package['files']),3)
        self.assertTrue(all(self.c.path(r).name=='Author.Scene.1.var' for r in package['files']))
        self.assertTrue(all(x['warnings'] for x in result['items']))

    def test_mismatched_identity_retained_with_warning_but_invalid_zip_rejected(self):
        imp=self.importer()
        ids=[self.upload(imp,'合集/File.Name.1.var',var_bytes()),
             self.upload(imp,'合集/Broken.Name.1.var',b'not a zip'),
             self.upload(imp,'合集/说明.txt',b'doc')]
        result=imp.run(ids,Job('import'),False,False)
        self.assertEqual((result['installed'],result['attachments'],result['failed']),(1,1,1))
        self.assertTrue(self.c.packages()['file.name.1']['warnings'])
        self.assertFalse((self.c.roots['active']/'包管家导入/合集/Broken.Name.1.var').exists())

    def test_same_relative_path_never_overwrites_or_renames_var(self):
        imp=self.importer()
        ids=[self.upload(imp,'合集/Author.Scene.1.var',var_bytes(description=str(n))) for n in range(2)]
        result=imp.run(ids,Job('import'),False,False)
        self.assertEqual((result['installed'],result['failed']),(2,0))
        files=list(self.c.roots['vault'].rglob('*.var'))
        self.assertEqual(len(files),2)
        self.assertTrue(all(f.name=='Author.Scene.1.var' for f in files))
        self.assertEqual(len({sha256(f) for f in files}),2)

    def test_partial_cancel_keeps_completed_files_indexed_and_cleans_uploads(self):
        imp=self.importer()
        ids=[self.upload(imp,f'合集/New.Item{n}.1.var',var_bytes(f'New.Item{n}.1')) for n in range(3)]
        job=Job('import')
        def move_and_cancel(src,dst,job=None):
            move_no_replace(src,dst);job.cancel.set()
        with mock.patch('vampm.importer.move_no_replace',side_effect=move_and_cancel):
            with self.assertRaises(Cancelled):imp.run(ids,job,False,False)
        self.assertEqual(self.c.stats()['packages'],1)
        self.assertFalse(list(imp.staging.iterdir()))
        report=json.loads(next((self.c.data/'import-history').glob('*.json')).read_text('utf-8'))
        self.assertEqual(report['installed'],1)

    def test_folder_path_validation_and_temporary_cleanup(self):
        imp=self.importer()
        for relative in ('../a.txt','root/../a.txt','/root/a.txt','C:/root/a.txt',
                         'root\\a.txt','a.txt','root/a:b.txt','root/sub./a.txt','root/a?.txt'):
            with self.subTest(relative=relative),self.assertRaises(ValueError):self.upload(imp,relative,b'text')
        with self.assertRaises(ValueError):imp.upload(io.BytesIO(b'text'),'a.txt',4)
        with self.assertRaises(ValueError):self.upload(imp,'root/test.exe',b'exe')
        with self.assertRaises(ValueError):self.upload(imp,'root/test.var',var_bytes())
        with self.assertRaises(ValueError):imp.upload(io.BytesIO(b''),'a.png',0,'root/other.png')
        uid=self.upload(imp,'root/empty.txt',b'');imp.discard([uid])
        self.assertFalse(list(imp.staging.iterdir()))

    def test_http_folder_upload_options_and_discard(self):
        app=Application(self.game,self.home);server=serve(app)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        base=f'http://127.0.0.1:{server.server_port}/api/'
        def request(route,data=None,mime='application/json'):
            req=urllib.request.Request(base+route,data=data,headers={'X-Session-Token':app.token,'Content-Type':mime})
            with urllib.request.urlopen(req) as r:return json.load(r)
        try:
            options=request('import-options');self.assertIn('.txt',options['extensions'])
            self.assertEqual(options['area'],'vault')
            q=urllib.parse.urlencode({'name':'说明.txt','relative_path':'人物/说明.txt'})
            result=request('upload?'+q,b'notes','application/octet-stream')
            self.assertTrue((app.importer.staging/(result['id']+'.bin')).exists())
            session=request('import-session',b'{}')['session']
            request('import-session/append',json.dumps({'session':session,'ids':[result['id']]}).encode())
            request('import-session/discard',json.dumps({'session':session}).encode())
            self.assertFalse(list(app.importer.staging.iterdir()))
        finally:server.shutdown();server.server_close();t.join()

    def test_large_session_preserves_folder_and_cleans_all_batches(self):
        imp=self.importer()
        session=imp.create_session()['session']
        contents={'合集/New.Look.1.var':var_bytes('New.Look.1'),
                  '合集/说明.txt':b'notes','合集/封面.png':b'cover'}
        ids=[self.upload(imp,p,b) for p,b in contents.items()]
        with mock.patch('vampm.importer.MAX_IMPORT_FILES',2):
            imp.append_session(session,ids[:2])
            imp.append_session(session,ids[2:])
            result=imp.run_session(session,Job('import'),False,False)
        self.assertEqual((result['installed'],result['attachments']),(1,2))
        for rel,data in contents.items():
            self.assertEqual((self.c.roots['vault']/'包管家导入'/rel).read_bytes(),data)
        self.assertFalse(list(imp.staging.iterdir()))

    def test_session_over_10000_ids_reaches_import_as_one_task(self):
        imp=self.importer()
        session=imp.create_session()['session']
        ids=[f'{i:032x}' for i in range(10001)]
        imp.append_session(session,ids[:10000])
        imp.append_session(session,ids[10000:])
        with mock.patch.object(imp,'_run',return_value={'installed':10001}) as run:
            result=imp.run_session(session,Job('import'),False,False)
        self.assertEqual(result['installed'],10001)
        self.assertEqual(run.call_args.args[0],ids)
        self.assertFalse(list(imp.staging.iterdir()))

    def test_session_cancel_discards_every_batch_and_validates_session_id(self):
        imp=self.importer()
        session=imp.create_session()['session']
        ids=[self.upload(imp,f'合集/{n}.txt',b'notes') for n in range(3)]
        with mock.patch('vampm.importer.MAX_IMPORT_FILES',2):
            imp.append_session(session,ids[:2])
            imp.append_session(session,ids[2:])
            imp.discard_session(session)
        self.assertFalse(list(imp.staging.iterdir()))
        for invalid in ('../outside','x'*32,None):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                imp.append_session(invalid,[ids[0]])

    def test_session_cancel_during_import_keeps_completed_packages(self):
        imp=self.importer()
        session=imp.create_session()['session']
        ids=[self.upload(imp,f'合集/New.Item{n}.1.var',var_bytes(f'New.Item{n}.1')) for n in range(3)]
        imp.append_session(session,ids)
        job=Job('import')
        def move_and_cancel(src,dst,job=None):
            move_no_replace(src,dst);job.cancel.set()
        with mock.patch('vampm.importer.move_no_replace',side_effect=move_and_cancel):
            with self.assertRaises(Cancelled):imp.run_session(session,job,False,False)
        self.assertEqual(self.c.stats()['packages'],1)
        self.assertFalse(list(imp.staging.iterdir()))

    def test_mixed_invalid_roots_are_skipped_without_blocking_valid_profile(self):
        self.put(deps=['Available.Hair.1']);self.put('Available.Hair.1',deps=['Absent.Asset.1'])
        self.put('Broken.Look.1',data=b'bad');self.scan()
        plan=self.apply(keys=['author.scene.1','broken.look.1'])
        self.assertEqual(plan['included_files'],2)
        self.assertTrue(plan['missing'])
        self.assertTrue(self.c.packages()['author.scene.1']['active'])
        self.assertTrue(self.c.packages()['available.hair.1']['active'])
        self.assertFalse(self.c.packages()['broken.look.1']['active'])
