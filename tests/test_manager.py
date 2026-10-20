import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vampm.catalog import Catalog, inspect_var
from vampm.importer import Importer
from vampm.operations import Operations, move_no_replace
from vampm.providers import AI, Downloads, approved_url
from vampm.server import Application, serve
from vampm.util import Job, atomic_json, matches, safe_path, sha256, split_id


def var_bytes(pkg='Author.Scene.1',deps=None,files=None,description=''):
    a,n,v=split_id(pkg)
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('meta.json',json.dumps({'creatorName':a,'packageName':n,'description':description,
            'dependencies':{k:{} for k in deps or []}}))
        for name,content in (files or {'Saves/scene/Test.json':'{}'}).items(): z.writestr(name,content)
    return b.getvalue()


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.game=self.root/'game'
        self.game.mkdir()
        self.home=self.root/'tool'
        self.c=Catalog(self.game,self.home)
        self.ops=Operations(self.c)
        self.patchers=[mock.patch('vampm.'+m+'.require_game_closed') for m in ('operations','providers','importer')]
        for p in self.patchers:p.start()

    def tearDown(self):
        for p in self.patchers:p.stop()
        self.tmp.cleanup()

    def put(self,pkg='Author.Scene.1',folder='',data=None,**kw):
        path=self.game/'AddonPackages'/folder/(pkg+'.var')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data or var_bytes(pkg,**kw))
        return path

    def scan(self,deep=False): return self.c.scan(Job('scan'),deep)

    def test_exact_latest_and_min_versions(self):
        self.assertTrue(matches('Alice.Look.latest','alice.LOOK.9.var'))
        self.assertTrue(matches('Alice.Look.min3','Alice.Look.5'))
        self.assertFalse(matches('Alice.Look.3','Alice.Look.5'))
        self.assertFalse(matches('Alice.Look.min8','Alice.Look.5'))
        self.assertIsNone(split_id('../x.1'))

    def test_scan_incremental_and_content_classification(self):
        self.put('作者.发型.1',files={'Custom/Hair/Female/Test/Hair.vam':'{}'})
        self.assertEqual(self.scan()['updated'],1)
        self.assertEqual(self.scan()['updated'],0)
        self.assertEqual(next(iter(self.c.packages().values()))['category'],'头发')

    def test_scan_handles_broken_zip_and_bad_meta(self):
        self.put('A.Broken.1',data=b'not a zip')
        path=self.put('A.Meta.1')
        with zipfile.ZipFile(path,'w') as z:z.writestr('meta.json','{oops')
        self.scan()
        self.assertEqual(self.c.stats()['errors'],2)

    def test_deep_scan_finds_undeclared_reference(self):
        self.put(files={'Saves/scene/Test.json':'{"file":"Other.Asset.4:/Custom/Asset/a.assetbundle"}'})
        self.scan()
        self.assertFalse(self.c.missing())
        self.scan(True)
        self.assertEqual(self.c.missing()[0]['ref'],'Other.Asset.4')

    def test_cycle_and_latest_dependency_closure(self):
        self.put('A.Scene.1',deps=['B.Asset.latest'])
        self.put('B.Asset.1')
        self.put('B.Asset.2',deps=['A.Scene.1'])
        self.scan()
        closure=self.c.closure(['a.scene.1'])
        self.assertEqual(closure['keys'],['a.scene.1','b.asset.2'])
        self.assertFalse(closure['missing'])

    def test_missing_exact_version_not_satisfied_by_newer(self):
        self.put('A.Scene.1',deps=['B.Asset.2'])
        self.put('B.Asset.3')
        self.scan()
        self.assertEqual(self.c.missing()[0]['ref'],'B.Asset.2')

    def test_manual_label_survives_rescan_and_versions(self):
        self.put();self.scan()
        self.c.label('author.scene.1',category='人物',summary='一套人物外观资源。',favorite=True)
        self.scan(True)
        p=self.c.packages()['author.scene.1']
        self.assertEqual(p['category'],'人物')
        self.assertTrue(p['favorite'])
        self.assertEqual(self.c.list({'q':'人物'})['total'],1)

    def test_duplicate_audit_preserves_conflicts_and_versions(self):
        data=var_bytes('A.Same.1')
        self.put('A.Same.1','a',data=data);self.put('A.Same.1','b',data=data)
        self.put('A.Same.2')
        self.put('B.Mixed.1','a');self.put('B.Mixed.1','b',description='different')
        self.scan()
        audit=self.c.duplicate_audit(Job('audit'))
        self.assertEqual(audit['extras'],1);self.assertEqual(audit['conflicts'],1)
        plan=self.ops.plan('dedup',[],Job('plan'))
        self.assertEqual(plan['count'],1)
        self.ops.apply(plan['id'],Job('apply'))
        self.assertEqual(self.c.stats()['files'],4)
        self.ops.undo(plan['id'],Job('undo'))
        self.assertEqual(self.c.stats()['files'],5)

    def test_dedup_aborts_if_keeper_removed_after_preview(self):
        data=var_bytes()
        self.put(folder='a',data=data);self.put(folder='b',data=data)
        self.scan();self.c.duplicate_audit(Job('audit'))
        p=self.ops.plan('dedup',[],Job('plan'))
        keep=self.ops.plans[p['id']]['keeps'][0]
        safe_path(self.c.roots[keep['area']],keep['rel']).unlink()
        with self.assertRaises(ValueError): self.ops.apply(p['id'],Job('apply'))
        self.assertEqual(len(list((self.game/'AddonPackages').rglob('*.var'))),1)

    def test_activate_closure_and_restore_all(self):
        self.put('A.Scene.1',deps=['B.Asset.1']);self.put('B.Asset.1');self.put('C.Other.1')
        self.scan()
        p=self.ops.plan('activate',['a.scene.1'],Job('plan'))
        self.assertEqual(p['included'],2)
        self.ops.apply(p['id'],Job('apply'))
        self.assertFalse(self.c.packages()['c.other.1']['active'])
        self.assertFalse(self.c.missing())
        p2=self.ops.plan('restore_all',[],Job('plan'))
        self.ops.apply(p2['id'],Job('apply'))
        self.assertEqual(self.c.stats()['active'],3)

    def test_activation_missing_dependencies_are_warnings(self):
        self.put(deps=['Missing.Hair.9','Local.Hair.1']);self.put('Local.Hair.1');self.put('Other.Scene.1');self.scan()
        p=self.ops.plan('activate',['author.scene.1'],Job('plan'))
        self.assertTrue(p['missing'])
        self.ops.apply(p['id'],Job('apply'))
        self.assertEqual(self.c.stats()['active'],2)
        self.assertTrue(self.c.packages()['local.hair.1']['active'])
        self.assertFalse(self.c.packages()['other.scene.1']['active'])
        self.assertEqual(self.ops.history()[0]['missing'],['Missing.Hair.9'])
        self.ops.undo(p['id'],Job('undo'))
        self.assertEqual(self.c.stats()['active'],3)

    def test_activation_same_id_conflict_requires_choice(self):
        self.put(folder='a');self.put(folder='b',description='different');self.scan()
        plan=self.ops.plan('activate',['author.scene.1'],Job('plan'))
        self.assertEqual(plan['unresolved_copies'],['author.scene.1'])
        with self.assertRaisesRegex(ValueError,'同名副本待选择'):self.ops.apply(plan['id'],Job('apply'))

    def test_stale_plan_never_moves_changed_files(self):
        path=self.put();self.scan()
        p=self.ops.plan('organize',['author.scene.1'],Job('plan'))
        path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(ValueError):self.ops.apply(p['id'],Job('apply'))
        self.assertTrue(path.exists())

    def test_organize_and_undo_retains_original_hierarchy(self):
        original=self.put(folder='自定义包/人物卡');self.scan()
        p=self.ops.plan('organize',['author.scene.1'],Job('plan'))
        self.ops.apply(p['id'],Job('apply'))
        self.assertFalse(original.exists())
        self.assertTrue((self.game/'.DisabledPackages/场景/Author.Scene.1.var').exists())
        self.ops.undo(p['id'],Job('undo'))
        self.assertTrue(original.exists())

    def test_interrupted_move_recovered_from_intention(self):
        self.put();self.scan()
        p=self.ops.plan('organize',['author.scene.1'],Job('plan'))
        full=self.ops.plans[p['id']]
        folder=self.ops.directory/p['id'];folder.mkdir()
        atomic_json(folder/'manifest.json',{**full,'status':'running','completed':0})
        self.ops._event(folder/'events.jsonl',index=0,phase='intent')
        m=full['moves'][0]
        move_no_replace(self.ops._path(m,'from'),self.ops._path(m,'to'))
        with self.assertRaises(ValueError):self.ops.check_pending()
        self.ops.undo(p['id'],Job('recovery'))
        self.assertTrue((self.game/'AddonPackages/Author.Scene.1.var').exists())
        self.ops.check_pending()

    def test_partial_apply_failure_is_undoable(self):
        self.put('A.Root.1');self.put('B.Other.1');self.put('C.Other.1');self.scan()
        p=self.ops.plan('activate',['a.root.1'],Job('plan'))
        actual=move_no_replace
        calls=[]
        def fail_second(src,dst,job=None):
            calls.append(1)
            if len(calls)==2:raise OSError('simulated disk failure')
            actual(src,dst)
        with mock.patch('vampm.operations.move_no_replace',side_effect=fail_second):
            with self.assertRaises(OSError):self.ops.apply(p['id'],Job('apply'))
        self.assertEqual(self.ops.history()[0]['status'],'interrupted')
        self.ops.undo(p['id'],Job('undo'))
        self.assertEqual(self.c.stats()['active'],3)

    def test_recovery_never_overwrites_new_file(self):
        original=self.put();self.scan()
        p=self.ops.plan('organize',['author.scene.1'],Job('plan'));self.ops.apply(p['id'],Job('apply'))
        original.write_bytes(b'new unrelated file')
        with self.assertRaises(ValueError):self.ops.undo(p['id'],Job('undo'))
        self.assertEqual(original.read_bytes(),b'new unrelated file')

    def test_safe_paths_and_no_overwrite(self):
        for path in ['../escape.var','/absolute.var','x/../../outside','C:/file.var','x\\a.var']:
            with self.assertRaises(ValueError):safe_path(self.root,path)
        src=self.root/'src';dst=self.root/'dst'
        src.write_bytes(b'one');dst.write_bytes(b'two')
        with self.assertRaises(ValueError):move_no_replace(src,dst)
        self.assertEqual(dst.read_bytes(),b'two')

    @unittest.skipIf(os.name=='nt','Creating symlinks needs privileges on Windows')
    def test_linked_directories_not_followed(self):
        outside=self.root/'outside';outside.mkdir()
        (self.game/'AddonPackages/link').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):safe_path(self.game/'AddonPackages','link/file.var')
        (outside/'A.B.1.var').write_bytes(var_bytes('A.B.1'))
        self.scan();self.assertEqual(self.c.stats()['files'],0)

    def test_ai_response_validation(self):
        good={'items':[{'key':'a.b.1','category':'人物','summary':'包含一套人物外观预设。'}]}
        self.assertIn('a.b.1',AI.validate(good,{'a.b.1'}))
        with self.assertRaises(ValueError):AI.validate(good,{'c.d.1'})
        with self.assertRaises(ValueError):AI.validate({'items':[]},{'a.b.1'})
        good['items'][0]['summary']='English only'
        with self.assertRaises(ValueError):AI.validate(good,{'a.b.1'})

    def test_trusted_hosts_and_api_url(self):
        self.assertTrue(approved_url('https://github.com/acidbubbles/vam-timeline/releases/download/x/a.var'))
        for url in ['http://hub.virtamate.com/a','https://hub.virtamate.com.evil.com/a','https://evil.com/a','https://user@github.com/a','https://github.com:9999/a']:
            self.assertFalse(approved_url(url))
        self.assertEqual(AI.api_url({'base_url':'https://api.deepseek.com'}),'https://api.deepseek.com/chat/completions')
        with self.assertRaises(ValueError):AI.api_url({'base_url':'http://example.com'})

    def test_download_validates_then_installs(self):
        data=var_bytes('Test.Asset.1')
        class Response(io.BytesIO):
            headers={'Content-Length':str(len(data))}
        d=Downloads(self.c)
        item={'filename':'Test.Asset.1.var','url':'https://hub.virtamate.com/a','bytes':len(data),'refs':['Test.Asset.1']}
        with mock.patch('vampm.providers.trusted_open',return_value=Response(data)):
            result=d.download_one(item,Job('download'))
        self.assertEqual(result['status'],'installed')
        self.assertEqual((self.game/'AddonPackages/包管家补充/Test.Asset.1.var').read_bytes(),data)

    def test_download_rejects_html(self):
        d=Downloads(self.c)
        class Response(io.BytesIO):headers={}
        item={'filename':'Test.Asset.1.var','url':'https://hub.virtamate.com/a','bytes':0,'refs':['Test.Asset.1']}
        for data in [b'<html>login required</html>']:
            with mock.patch('vampm.providers.trusted_open',return_value=Response(data)):
                with self.assertRaises(Exception):d.download_one(item,Job('download'))
        self.assertFalse(list((self.game/'AddonPackages').rglob('*.var')))

    def test_import_auto_classifies_and_skips_existing(self):
        d=Downloads(self.c);a=AI(self.c);imp=Importer(self.c,a,d)
        data=var_bytes('New.Hair.1',files={'Custom/Hair/Female/a.vam':'{}'})
        upload=imp.upload(io.BytesIO(data),'New.Hair.1.var',len(data))
        result=imp.run([upload['id']],Job('import'),use_ai=False,find=False)
        self.assertEqual(result['installed'],1)
        self.assertTrue((self.game/'.DisabledPackages/包管家导入/头发/New.Hair.1.var').exists())
        upload=imp.upload(io.BytesIO(data),'New.Hair.1.var',len(data))
        result=imp.run([upload['id']],Job('import'),use_ai=False,find=False)
        self.assertEqual(result['skipped'],1)

    def test_import_ai_failure_does_not_lose_package(self):
        d=Downloads(self.c);a=AI(self.c);imp=Importer(self.c,a,d)
        data=var_bytes()
        upload=imp.upload(io.BytesIO(data),'Author.Scene.1.var',len(data))
        with mock.patch.object(a,'classify_batch',side_effect=ValueError('no quota')):
            result=imp.run([upload['id']],Job('import'),use_ai=True,find=False)
        self.assertEqual(result['installed'],1)
        self.assertEqual(result['ai_error'],'no quota')

    def test_import_ai_label_is_used_for_directory(self):
        d=Downloads(self.c);a=AI(self.c);imp=Importer(self.c,a,d)
        data=var_bytes();upload=imp.upload(io.BytesIO(data),'Author.Scene.1.var',len(data))
        with mock.patch.object(a,'classify_batch',return_value={'author.scene.1':{'category':'人物','summary':'包含人物外观展示场景。'}}):
            result=imp.run([upload['id']],Job('import'),use_ai=True,find=False)
        self.assertEqual(result['installed'],1)
        self.assertTrue((self.game/'.DisabledPackages/包管家导入/人物/Author.Scene.1.var').exists())
        self.assertEqual(self.c.packages()['author.scene.1']['source'],'ai')

    def test_local_http_requires_token_and_valid_origin(self):
        app=Application(self.game,self.home)
        server=serve(app)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        url=f'http://127.0.0.1:{server.server_port}/api/status'
        try:
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(url)
            req=urllib.request.Request(url,headers={'X-Session-Token':app.token})
            with urllib.request.urlopen(req) as r:self.assertEqual(r.status,200)
            req=urllib.request.Request(url,headers={'X-Session-Token':app.token,'Origin':'https://evil.com'})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
        finally:server.shutdown();server.server_close();t.join()

    def test_new_external_package_invalidates_activation_preview(self):
        self.put('A.Root.1');self.put('B.Other.1');self.scan()
        plan=self.ops.plan('activate',['a.root.1'],Job('plan'))
        self.put('New.External.1')
        with self.assertRaises(ValueError):self.ops.apply(plan['id'],Job('apply'))
        self.assertEqual(self.c.stats()['active'],3)

    def test_hub_wrong_version_is_not_offered(self):
        self.put(deps=['Other.Package.1']);self.scan()
        data={'packages':{'Other.Package.1':{'filename':'Other.Package.9.var',
            'downloadUrl':'https://hub.virtamate.com/download','file_size':12}}}
        with mock.patch('vampm.providers.trusted_json',return_value=data):
            d=Downloads(self.c);result=d.find(Job('sources'))
        self.assertEqual(result['available'],0)
        self.assertEqual(result['unresolved'],1)

    def test_compatible_api_http_round_trip_and_no_key_redirect(self):
        from http.server import BaseHTTPRequestHandler,HTTPServer
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            redirect=False
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,self.headers.get('Authorization'),payload))
                if self.redirect:
                    self.send_response(302);self.send_header('Location','http://localhost:9/steal');self.end_headers();return
                result={'choices':[{'message':{'content':json.dumps({'items':[{'key':'a.b.1','category':'头发','summary':'可加载到人物上的短发资源。'}]})}}]}
                data=json.dumps(result).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        ai=AI(self.c)
        ai.save({'provider':'compatible','base_url':f'http://127.0.0.1:{server.server_port}','api_model':'test-model'})
        try:
            with mock.patch.dict(os.environ,{'VAMPM_API_KEY':'test-only-key'}):
                out=ai.classify_batch([{'key':'a.b.1'}],Job('test'))
                self.assertEqual(out['a.b.1']['category'],'头发')
                self.assertEqual(requests[0][1],'Bearer test-only-key')
                self.assertEqual(requests[0][2]['response_format'],{'type':'json_object'})
                Handler.redirect=True
                with self.assertRaises(ValueError):ai.classify_batch([{'key':'a.b.1'}],Job('test'))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
